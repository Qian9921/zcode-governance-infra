"""Readiness state machine tests for :mod:`zgov.state`.

Reviewer model identity is never hard-coded: the tests pin a resolved roles
configuration through ``ZGOV_ROLES_PATH`` and build every fixture from
``review_policy.resolve_reviewer``.  The suite therefore behaves identically
whether or not the ambient environment already supplies a roles file.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest

from zgov.contracts import canonical_sha256
from zgov.review_policy import resolve_reviewer
from zgov.state import (
    ReadinessError,
    STATES,
    StateStore,
    initial_state,
    transition,
    validate_state,
)

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
TREE_SHA = "c" * 40
CLOSURE_SHA = "d" * 64
COUNTEREXAMPLES = ["CE-001", "CE-002"]
GREEN_COUNTS = {"total": 3, "ran": 3, "passed": 3, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}

RESOLVED_ROLES = {
    "schema": "gov-roles.v1",
    "roles": {
        "writer": "test-writer-model",
        "executor": "test-executor-model",
        "reviewer_standard": "test-reviewer-standard-model",
        "reviewer_high": "test-reviewer-high-model",
        "auditor_spark": "test-auditor-spark-model",
    },
    # Deliberately *not* the shipped default profile.  Readiness derives the
    # expected effort from this config, so pinning the defaults here would let a
    # hard-coded effort table in ``zgov.state`` pass unnoticed.
    "efforts": {
        "reviewer_standard": "medium",
        "reviewer_high": "high",
        "delta_continuation": "medium",
        "auditor_spark": "medium",
    },
    "agents": {},
}


def _timestamp(index: int) -> str:
    return "2099-01-01T00:%02d:00Z" % index


def _receipt(kind: str, stage: str, *, counts: dict[str, int] | None = None) -> dict[str, object]:
    prefix = "EVID" if kind == "evidence" else "GATE"
    artifact = {
        "receipt_id": f"{prefix}-{stage}-{HEAD_SHA}",
        "kind": kind,
        "stage": stage,
        "head_sha": HEAD_SHA,
        "tree_sha": TREE_SHA,
        "decision": "allow",
        "counts": dict(counts or GREEN_COUNTS),
        "artifact_sha256": "",
    }
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    return artifact


class ReadinessStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._roles_dir = tempfile.mkdtemp(prefix="zgov-state-roles-")
        roles_path = pathlib.Path(self._roles_dir) / "roles.json"
        roles_path.write_text(json.dumps(RESOLVED_ROLES), encoding="utf-8")
        self._previous_roles_env = os.environ.get("ZGOV_ROLES_PATH")
        os.environ["ZGOV_ROLES_PATH"] = str(roles_path)
        self.addCleanup(self._restore_roles_env)
        self.addCleanup(shutil.rmtree, self._roles_dir, True)

    def _restore_roles_env(self) -> None:
        if self._previous_roles_env is None:
            os.environ.pop("ZGOV_ROLES_PATH", None)
        else:
            os.environ["ZGOV_ROLES_PATH"] = self._previous_roles_env

    @staticmethod
    def _policy(risk: str, stages: list[str]) -> dict[str, object]:
        return {
            "review_risk": risk,
            "reasons": ["state machine unit test"],
            "classifier_identity": "test-classifier",
            "required_stages": list(stages),
        }

    def _draft(self, risk: str = "medium", stages: list[str] | None = None) -> dict[str, object]:
        stages = stages or ["targeted", "full"]
        return initial_state("MIS-STATE-MACHINE", BASE_SHA, TREE_SHA, self._policy(risk, stages))

    def _audited(self, *, spark_findings: list[str] | None = None, dispositions: dict[str, str] | None = None, risk: str = "medium", stages: list[str] | None = None) -> dict[str, object]:
        state = self._draft(risk, stages)
        state = transition(state, "COUNTEREXAMPLES_FROZEN", base_sha=BASE_SHA, head_sha=BASE_SHA, counterexample_ids=COUNTEREXAMPLES, updated_at=_timestamp(1))
        state = transition(state, "BASELINE_REPRODUCED", base_sha=BASE_SHA, head_sha=BASE_SHA, red_counterexamples=COUNTEREXAMPLES, updated_at=_timestamp(2))
        state = transition(state, "IMPLEMENTING", base_sha=BASE_SHA, head_sha=HEAD_SHA, tree_sha=TREE_SHA, green_counterexamples=COUNTEREXAMPLES, updated_at=_timestamp(3))
        return transition(
            state,
            "INNER_AUDIT_COMPLETE",
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            spark_audit_count=0 if spark_findings is None else 1,
            spark_findings=spark_findings,
            dispositions=dispositions,
            updated_at=_timestamp(4),
        )

    def _local_ready(self, **kwargs: object) -> dict[str, object]:
        audited = self._audited(**kwargs)
        evidence = _receipt("evidence", "targeted")
        return transition(
            audited,
            "LOCAL_READY",
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            author_closure_sha256=CLOSURE_SHA,
            evidence_ids=[evidence["receipt_id"]],
            receipt_artifacts={evidence["receipt_id"]: evidence},
            updated_at=_timestamp(5),
        )

    # --- transition legality -------------------------------------------------

    def test_adjacent_transition_is_allowed(self):
        draft = self._draft()
        self.assertEqual(draft["state"], "DRAFT")
        moved = transition(draft, "COUNTEREXAMPLES_FROZEN", base_sha=BASE_SHA, head_sha=BASE_SHA, counterexample_ids=COUNTEREXAMPLES, updated_at=_timestamp(1))
        self.assertEqual(moved["state"], "COUNTEREXAMPLES_FROZEN")
        self.assertEqual(moved["revision"], 1)
        self.assertEqual(STATES.index("COUNTEREXAMPLES_FROZEN") - STATES.index("DRAFT"), 1)

    def test_skipping_states_is_rejected(self):
        draft = self._draft()
        with self.assertRaises(ReadinessError) as caught:
            transition(draft, "LOCAL_READY", base_sha=BASE_SHA, head_sha=BASE_SHA, updated_at=_timestamp(1))
        self.assertIn("illegal state jump DRAFT -> LOCAL_READY", str(caught.exception))

    def test_backward_transition_is_rejected(self):
        ready = self._local_ready()
        with self.assertRaises(ReadinessError) as caught:
            transition(ready, "IMPLEMENTING", base_sha=BASE_SHA, head_sha=HEAD_SHA, updated_at=_timestamp(6))
        self.assertIn("illegal state jump LOCAL_READY -> IMPLEMENTING", str(caught.exception))

    # --- author cannot self-assert review readiness --------------------------

    def test_author_cannot_self_assert_review_ready(self):
        audited = self._audited()
        evidence = _receipt("evidence", "targeted")
        with self.assertRaises(ReadinessError) as caught:
            transition(
                audited,
                "LOCAL_READY",
                base_sha=BASE_SHA,
                head_sha=HEAD_SHA,
                author_closure_sha256=CLOSURE_SHA,
                evidence_ids=[evidence["receipt_id"]],
                receipt_artifacts={evidence["receipt_id"]: evidence},
                review_ready=True,
                updated_at=_timestamp(5),
            )
        self.assertIn("author cannot self-assert review_ready", str(caught.exception))

    def test_review_ready_requires_external_independent_artifact(self):
        ready = self._local_ready()
        evidence_targeted = _receipt("evidence", "targeted")
        evidence_full = _receipt("evidence", "full")
        gate_targeted = _receipt("gate", "targeted")
        gate_full = _receipt("gate", "full")
        artifacts = {a["receipt_id"]: a for a in (evidence_targeted, evidence_full, gate_targeted, gate_full)}
        fresh = transition(
            ready,
            "FRESH_READY",
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            evidence_ids=[evidence_targeted["receipt_id"], evidence_full["receipt_id"]],
            gate_ids=[gate_targeted["receipt_id"], gate_full["receipt_id"]],
            receipt_artifacts=artifacts,
            updated_at=_timestamp(6),
        )
        self.assertFalse(fresh["review_ready"])
        with self.assertRaises(ReadinessError) as caught:
            transition(
                fresh,
                "REVIEW_READY",
                base_sha=BASE_SHA,
                head_sha=HEAD_SHA,
                evidence_ids=[evidence_targeted["receipt_id"], evidence_full["receipt_id"]],
                gate_ids=[gate_targeted["receipt_id"], gate_full["receipt_id"]],
                receipt_artifacts=artifacts,
                updated_at=_timestamp(7),
            )
        self.assertIn("author review packet and formal Independent artifact required", str(caught.exception))

    # --- timestamps ----------------------------------------------------------

    def test_backdating_is_rejected(self):
        draft = self._draft()
        moved = transition(draft, "COUNTEREXAMPLES_FROZEN", base_sha=BASE_SHA, head_sha=BASE_SHA, counterexample_ids=COUNTEREXAMPLES, updated_at=_timestamp(5))
        with self.assertRaises(ReadinessError) as caught:
            transition(moved, "BASELINE_REPRODUCED", base_sha=BASE_SHA, head_sha=BASE_SHA, red_counterexamples=COUNTEREXAMPLES, updated_at=_timestamp(2))
        self.assertIn("backdating", str(caught.exception))

    # --- receipts ------------------------------------------------------------

    def test_missing_receipt_for_required_stage_is_rejected(self):
        ready = self._local_ready()
        evidence_targeted = _receipt("evidence", "targeted")
        gate_targeted = _receipt("gate", "targeted")
        gate_full = _receipt("gate", "full")
        artifacts = {a["receipt_id"]: a for a in (evidence_targeted, gate_targeted, gate_full)}
        with self.assertRaises(ReadinessError) as caught:
            transition(
                ready,
                "FRESH_READY",
                base_sha=BASE_SHA,
                head_sha=HEAD_SHA,
                evidence_ids=[evidence_targeted["receipt_id"]],
                gate_ids=[gate_targeted["receipt_id"], gate_full["receipt_id"]],
                receipt_artifacts=artifacts,
                updated_at=_timestamp(6),
            )
        self.assertIn("receipt stages must equal frozen review policy", str(caught.exception))

    def test_extra_receipt_beyond_required_stages_is_rejected(self):
        ready = self._local_ready()
        budgeted = [_receipt("evidence", stage) for stage in ("targeted", "full")]
        unbudgeted = _receipt("evidence", "fresh")
        gates = [_receipt("gate", stage) for stage in ("targeted", "full")]
        cases = (
            ("listed", [a["receipt_id"] for a in budgeted + [unbudgeted]], "receipt ID is not bound to a validated artifact"),
            ("carried", [a["receipt_id"] for a in budgeted], "receipt stage exceeds frozen review policy"),
        )
        artifacts = {a["receipt_id"]: a for a in budgeted + [unbudgeted] + gates}
        for label, evidence_ids, message in cases:
            with self.subTest(extra=label):
                with self.assertRaises(ReadinessError) as caught:
                    transition(
                        ready,
                        "FRESH_READY",
                        base_sha=BASE_SHA,
                        head_sha=HEAD_SHA,
                        evidence_ids=evidence_ids,
                        gate_ids=[a["receipt_id"] for a in gates],
                        receipt_artifacts=artifacts,
                        updated_at=_timestamp(6),
                    )
                self.assertIn(message, str(caught.exception))

    def test_non_green_receipt_counts_are_rejected(self):
        audited = self._audited()
        for label, counts in (
            ("failed", {"total": 3, "ran": 3, "passed": 2, "failed": 1, "skipped": 0, "xfail": 0, "unknown": 0}),
            ("skipped", {"total": 3, "ran": 2, "passed": 2, "failed": 0, "skipped": 1, "xfail": 0, "unknown": 0}),
        ):
            with self.subTest(counts=label):
                evidence = _receipt("evidence", "targeted", counts=counts)
                with self.assertRaises(ReadinessError) as caught:
                    transition(
                        audited,
                        "LOCAL_READY",
                        base_sha=BASE_SHA,
                        head_sha=HEAD_SHA,
                        author_closure_sha256=CLOSURE_SHA,
                        evidence_ids=[evidence["receipt_id"]],
                        receipt_artifacts={evidence["receipt_id"]: evidence},
                        updated_at=_timestamp(5),
                    )
                self.assertIn("receipt artifact counts are not green", str(caught.exception))

    # --- Spark ---------------------------------------------------------------

    def test_active_spark_follow_up_blocks_readiness(self):
        with self.assertRaises(ReadinessError) as caught:
            self._local_ready(spark_findings=["SPARK-001"], dispositions={"SPARK-001": "FOLLOW_UP"})
        self.assertIn("active Spark FOLLOW_UP prevents readiness", str(caught.exception))

    # --- frozen review policy identity ---------------------------------------

    def test_frozen_policy_identity_drift_is_rejected(self):
        ready = self._local_ready()
        drifted = dict(ready)
        drifted["required_stages"] = ["targeted"]
        with self.assertRaises(ReadinessError) as caught:
            validate_state(drifted)
        self.assertIn("review policy identity mismatch", str(caught.exception))

    def test_reviewer_model_is_bound_to_resolved_roles(self):
        for risk, stages in (("low", ["targeted"]), ("medium", ["targeted", "full"]), ("high", ["targeted", "full", "fresh"])):
            with self.subTest(risk=risk):
                policy = self._policy(risk, stages)
                if risk == "high":
                    policy["high_risk_triggers"] = ["security"]
                state = initial_state("MIS-STATE-MACHINE", BASE_SHA, TREE_SHA, policy)
                expected = resolve_reviewer(risk)
                self.assertEqual(state["reviewer_model"], expected["model"])
                self.assertEqual(state["reasoning_effort"], expected["reasoning_effort"])
                self.assertEqual(state["reviewer_route"], "high_risk" if risk == "high" else "general")
                drifted = dict(state)
                drifted["reviewer_model"] = expected["model"] + "-tampered"
                with self.assertRaises(ReadinessError):
                    validate_state(drifted)

    # --- store ---------------------------------------------------------------

    def test_state_store_round_trip(self):
        root = tempfile.mkdtemp(prefix="zgov-state-store-")
        self.addCleanup(shutil.rmtree, root, True)
        store = StateStore(root)
        ready = self._local_ready()
        digest = store.save(ready)
        self.assertEqual(len(digest), 64)
        self.assertEqual(store.load(), ready)

    def test_state_store_rejects_symlinked_state_file(self):
        root = tempfile.mkdtemp(prefix="zgov-state-store-")
        self.addCleanup(shutil.rmtree, root, True)
        target = pathlib.Path(root) / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        os.symlink(target, pathlib.Path(root) / "readiness-state.json")
        with self.assertRaises(ReadinessError) as caught:
            StateStore(root)
        self.assertIn("state symlink forbidden", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
