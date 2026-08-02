"""Verdict-gate, P1/BLOCKING, first-round and escalation-drift tests for zgov.trace."""

import copy
import unittest

from zgov.contracts import build_pre_execution_closure_authority, canonical_sha256
from zgov.review_policy import resolve_reviewer
from zgov.trace import (
    TraceError,
    _counterexample_sha256,
    _evidence_result_sha256,
    ingest_independent_artifact,
    render_pr_trace,
    validate_review_packet,
)

BASE = "e18439c8dfe01d901895efd09b8b73b6842327a9"
HEAD = "0123456789abcdef0123456789abcdef01234567"
TREE = "1de79a7c48e6c66f167be54ca9cf387310149f80"
LINEAGE = {
    "dispatch_transcript_sha256": "d" * 64,
    "task_id": "/root/final-review",
    "parent_task_id": "/root",
    "sender": "/root/final-review",
}
_CLOSURE_CASES = ("R-1",)


def sha(index):
    return f"{index:064x}"


def route_for(risk):
    reviewer = resolve_reviewer(risk)
    route = "high_risk" if risk == "high" else "general"
    return route, reviewer["model"], reviewer["reasoning_effort"]


def closure_binding_receipt():
    bindings = []
    for finding_id in _CLOSURE_CASES:
        binding = {
            "finding_id": finding_id,
            "counterexample_id": "CE-EVIDENCE",
            "executable_counterexample_id": "NF-001",
            "counterexample_sha256": _counterexample_sha256("deterministic counterexample"),
            "gate_id": "G-TARGETED",
            "stage": "targeted",
            "evidence_row_id": f"EVID-G-TARGETED-EP-{finding_id}",
            "entrypoint_id": f"EP-{finding_id}",
            "binding_sha256": "",
        }
        binding["binding_sha256"] = canonical_sha256(binding)
        bindings.append(binding)
    bindings.sort(key=lambda item: item["finding_id"])
    receipt = {
        "schema": "closure-binding-receipt.v16",
        "mission_id": "V16-PRODUCTIVITY",
        "compiled_plan_sha256": "a" * 64,
        "closure_plan_sha256": "b" * 64,
        "closure_plan_file_sha256": "c" * 64,
        "dispatch_transcript_file_sha256": "d" * 64,
        "normalized_source_artifacts": [
            {
                "audit_id": "SPARK-A",
                "artifact_path": "gov/zgov/contracts/spark_result_A.v16.json",
                "artifact_sha256": "e" * 64,
            },
        ],
        "finding_count": len(bindings),
        "bindings": bindings,
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def evidence_bundle(*, head, tree, receipt=None):
    if receipt is None:
        receipt = closure_binding_receipt()
    grouped = {}
    for binding in receipt["bindings"]:
        grouped.setdefault(binding["evidence_row_id"], []).append(binding)
    rows = []
    for index, (case_id, bindings) in enumerate(sorted(grouped.items())):
        binding = bindings[0]
        row = {
            "case_id": case_id,
            "gate_id": binding["gate_id"],
            "stage": binding["stage"],
            "entrypoint_id": binding["entrypoint_id"],
            "decision": "allow",
            "actual_head": head,
            "tree_sha": tree,
            "identity_mode": "git-exact-object",
            "snapshot_sha256": "",
            "dirty": False,
            "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
            "log_sha256": f"{index + 1:064x}",
        }
        if len(bindings) == 1:
            row["finding_id"] = binding["finding_id"]
            row["counterexample_sha256"] = binding["counterexample_sha256"]
        row["closure_binding_receipt_sha256"] = receipt["receipt_sha256"]
        row["closure_binding_sha256s"] = sorted(item["binding_sha256"] for item in bindings)
        rows.append(row)
    bundle = {
        "schema": "evidence-envelope.v16",
        "mission_id": "V16-PRODUCTIVITY",
        "head_sha": head,
        "tree_sha": tree,
        "identity_mode": "git-exact-object",
        "snapshot_sha256": "",
        "clean": True,
        "rows": rows,
        "closure_binding_receipt_sha256": receipt["receipt_sha256"],
        "closure_plan_sha256": receipt["closure_plan_sha256"],
        "envelope_sha256": "",
    }
    bundle["envelope_sha256"] = canonical_sha256(bundle)
    return bundle


def decision_basis(index=1, risk="low", *, closure_receipt=None):
    route, model, effort = route_for(risk)
    policy = {
        "required_stages": ["targeted"],
        "review_risk": risk,
        "reviewer_route": route,
        "reviewer_model": model,
        "reasoning_effort": effort,
        "classifier_identity": "classifier-v1",
        "high_risk_triggers": ["security"] if risk == "high" else [],
    }
    return {
        "acceptance_envelope_sha256": sha(index * 10 + 1),
        "diff_sha256": sha(index * 10 + 2),
        "reviewed_dependency_scope_sha256": sha(3),
        "evidence_bundle_sha256": sha(index * 10 + 4),
        "evidence_denominator": index + 1,
        "review_risk": risk,
        "reviewer_route": route,
        "reviewer_model": model,
        "reasoning_effort": effort,
        "required_stages": policy["required_stages"],
        "classifier_identity": policy["classifier_identity"],
        "high_risk_triggers": policy["high_risk_triggers"],
        "review_policy_sha256": canonical_sha256(policy),
        "reference_identity_sha256": sha(index * 10 + 5),
        "operating_domain_sha256": sha(index * 10 + 6),
        "acceptance_thresholds_sha256": sha(index * 10 + 7),
        "invariants_sha256": sha(index * 10 + 8),
        "non_goals_sha256": sha(index * 10 + 9),
        "closure_authority": build_pre_execution_closure_authority(
            closure_receipt or closure_binding_receipt()
        ),
    }


def full_finding(fid, *, severity="P2", label=None, disposition="FOLLOW_UP", blocker_admission="NONE", admission_evidence_ref=""):
    return {
        "id": fid,
        "severity": severity,
        "label": ("BLOCKING" if severity == "P1" else "FOLLOW_UP") if label is None else label,
        "attribution": "DELTA_INTRODUCED" if severity == "P1" else "ORIGINAL_SCOPE_MISSED",
        "location": "gov/zgov/trace.py:1",
        "contract_clause": "REVIEW-1 finding contract",
        "counterexample": "deterministic counterexample",
        "impact": "approval could be unsound",
        "smallest_acceptable_outcome": "reject malformed artifact",
        "acceptance_check": "focused trace test",
        "blocker_admission": blocker_admission,
        "admission_evidence_ref": admission_evidence_ref,
        "disposition": disposition,
    }


def legacy_finding(fid, *, severity="P2", label="FOLLOW_UP", disposition="OPEN"):
    return {
        "id": fid,
        "severity": severity,
        "label": label,
        "attribution": "ORIGINAL_SCOPE_MISSED",
        "location": "gov/zgov/trace.py:1",
        "counterexample": "deterministic counterexample",
        "disposition": disposition,
    }


CHECK = {
    "id": "CHK-1",
    "status": "GREEN",
    "reused": False,
    "skipped": False,
    "cost": "tiny",
    "denominator": 1,
    "total": 1,
    "passed": 1,
    "failed": 0,
}


def trace_packet(*, head=HEAD, tree=TREE, basis=None, reviewed_scope=None, unreviewed_scope=None, bundle=None):
    basis = copy.deepcopy(decision_basis(1) if basis is None else basis)
    if bundle is None:
        bundle = evidence_bundle(head=head, tree=tree)
    basis["evidence_bundle_sha256"] = bundle["envelope_sha256"]
    return render_pr_trace(
        mission_id="V16-PRODUCTIVITY",
        base_sha=BASE,
        head_sha=head,
        tree_sha=tree,
        checks=[dict(CHECK)],
        findings=[],
        closures={},
        reviewed_scope=["gov/zgov"] if reviewed_scope is None else reviewed_scope,
        unreviewed_scope=[] if unreviewed_scope is None else unreviewed_scope,
        decision_basis=basis,
    )["packet"]


def seal_artifact(artifact):
    artifact["findings_sha256"] = canonical_sha256(artifact["findings"])
    artifact["closures_sha256"] = canonical_sha256(artifact["closures"])
    artifact["artifact_sha256"] = ""
    artifact["artifact_sha256"] = canonical_sha256(artifact)
    return artifact


def formal_artifact(
    packet,
    *,
    mode="independent_clean_room",
    verdict="APPROVE",
    findings=None,
    prior=None,
    continuity="reviewer-A",
    run_id="review-run-1",
    lineage=None,
    trigger=None,
):
    basis = packet["decision_basis"]
    reviewer_findings = [] if findings is None else copy.deepcopy(findings)
    prior_findings = {} if prior is None else {
        finding["id"]: {
            "prior_disposition": finding["disposition"],
            "disposition": finding["disposition"],
            "evidence_ref": "prior-evidence",
            "counterexample_recheck": finding["counterexample"],
        }
        for finding in prior["findings"]
    }
    artifact = {
        "schema": "independent-review.v16",
        "reviewer_login": "Liang9921",
        "reviewer_model": basis["reviewer_model"],
        "reasoning_effort": basis["reasoning_effort"],
        "reviewer_route": basis["reviewer_route"],
        "review_risk": basis["review_risk"],
        "fork_turns": "none",
        "context_mode": mode,
        "report_only": True,
        "reviewer_is_writer": False,
        "base_sha": packet["base_sha"],
        "head_sha": packet["head_sha"],
        "tree_sha": packet["tree_sha"],
        "diff_sha256": basis["diff_sha256"],
        "coverage_status": packet["coverage_status"],
        "reviewed_scope": list(packet["reviewed_scope"]),
        "unreviewed_scope": list(packet["unreviewed_scope"]),
        "review_packet_sha256": canonical_sha256(packet),
        "acceptance_envelope_sha256": basis["acceptance_envelope_sha256"],
        "reviewed_dependency_scope_sha256": basis["reviewed_dependency_scope_sha256"],
        "evidence_bundle_sha256": basis["evidence_bundle_sha256"],
        "evidence_denominator": basis["evidence_denominator"],
        "prior_review_artifact_sha256": None if prior is None else prior["artifact_sha256"],
        "prior_head_sha": None if prior is None else prior["head_sha"],
        "delta_sha256": None if prior is None else sha(90),
        "reviewer_continuity_id": continuity,
        "run_id": run_id,
        "escalation_trigger": trigger,
        "escalation_evidence_ref": "",
        "findings": reviewer_findings,
        "findings_sha256": "",
        "closures": {finding["id"]: finding["disposition"] for finding in reviewer_findings},
        "closures_sha256": "",
        "known_limitations": ["P2 follow-up remains visible"] if reviewer_findings else [],
        "dispatch_lineage": dict(LINEAGE if lineage is None else lineage),
        "verdict": verdict,
        "artifact_sha256": "",
        "required_stages": list(basis["required_stages"]),
        "classifier_identity": basis["classifier_identity"],
        "high_risk_triggers": list(basis["high_risk_triggers"]),
        "review_policy_sha256": basis["review_policy_sha256"],
        "reference_identity_sha256": basis["reference_identity_sha256"],
        "operating_domain_sha256": basis["operating_domain_sha256"],
        "acceptance_thresholds_sha256": basis["acceptance_thresholds_sha256"],
        "invariants_sha256": basis["invariants_sha256"],
        "non_goals_sha256": basis["non_goals_sha256"],
        "closure_matrix": prior_findings,
        "closure_matrix_sha256": canonical_sha256(prior_findings),
    }
    return seal_artifact(artifact)


def ingest(packet, artifact, **kwargs):
    if artifact["context_mode"] != "independent_clean_room":
        kwargs.setdefault("expected_delta_sha256", artifact["delta_sha256"])
    if artifact["context_mode"] == "escalated_fresh":
        kwargs.setdefault("expected_escalation_evidence_ref", artifact["escalation_evidence_ref"])
    receipt = kwargs.setdefault("closure_binding_receipt", closure_binding_receipt())
    kwargs.setdefault(
        "evidence_bundle",
        evidence_bundle(head=packet["head_sha"], tree=packet["tree_sha"], receipt=receipt),
    )
    return ingest_independent_artifact(
        packet,
        artifact,
        expected_dispatch_transcript_sha256=artifact["dispatch_lineage"]["dispatch_transcript_sha256"],
        expected_task_id=artifact["dispatch_lineage"]["task_id"],
        expected_parent_task_id=artifact["dispatch_lineage"]["parent_task_id"],
        expected_sender=artifact["dispatch_lineage"]["sender"],
        expected_review_risk=packet["decision_basis"]["review_risk"],
        expected_reviewer_route=packet["decision_basis"]["reviewer_route"],
        expected_reviewer_model=packet["decision_basis"]["reviewer_model"],
        expected_reasoning_effort=packet["decision_basis"]["reasoning_effort"],
        **kwargs,
    )


def raw_packet(*, findings=None, verdict=None, coverage="COMPLETE", unreviewed=None, artifact_sha=None):
    findings = [] if findings is None else findings
    packet = {
        "schema": "review-packet.v16",
        "mission_id": "V16-PRODUCTIVITY",
        "author_login": "Qian9921",
        "reviewer_login": "Liang9921",
        "base_sha": BASE,
        "head_sha": HEAD,
        "tree_sha": TREE,
        "lineage_mode": "DISPATCH_TRANSCRIPT",
        "coverage_status": coverage,
        "reviewed_scope": ["gov/zgov"],
        "unreviewed_scope": [] if unreviewed is None else list(unreviewed),
        "checks": [dict(CHECK, ran=1, unknown=0, xfail=0)],
        "findings": [dict(finding) for finding in findings],
        "closures": {finding["id"]: finding["disposition"] for finding in findings},
        "verdict": verdict,
        "round": 1,
        "body_sha256": sha(7),
    }
    if artifact_sha is not None:
        packet["independent_artifact_sha256"] = artifact_sha
    return packet


class P1BlockingDualityTests(unittest.TestCase):
    def test_p1_without_blocking_label_is_rejected(self):
        with self.assertRaisesRegex(TraceError, "P1 must be BLOCKING"):
            validate_review_packet(
                raw_packet(findings=[legacy_finding("F-1", severity="P1", label="NON_BLOCKING")], verdict=None)
            )

    def test_blocking_label_without_p1_is_rejected(self):
        with self.assertRaisesRegex(TraceError, "P2/P3 cannot be BLOCKING"):
            validate_review_packet(
                raw_packet(findings=[legacy_finding("F-1", severity="P2", label="BLOCKING")], verdict=None)
            )

    def test_non_p1_cannot_carry_blocker_admission(self):
        packet = trace_packet()
        finding = full_finding(
            "R-1",
            severity="P2",
            blocker_admission="DELTA_INTRODUCED",
            admission_evidence_ref="CE-1",
        )
        artifact = formal_artifact(packet, verdict="APPROVE", findings=[finding])
        with self.assertRaisesRegex(TraceError, "only P1 may carry blocker admission"):
            ingest(packet, artifact)

    def test_p1_blocking_with_admission_is_accepted(self):
        packet = trace_packet()
        finding = full_finding(
            "R-1",
            severity="P1",
            disposition="OPEN",
            blocker_admission="DELTA_INTRODUCED",
            admission_evidence_ref="CE-1",
        )
        artifact = formal_artifact(packet, verdict="REQUEST_CHANGES", findings=[finding])
        result = ingest(packet, artifact)
        self.assertEqual(result["verdict"], "REQUEST_CHANGES")
        self.assertEqual(result["findings"][0]["severity"], "P1")
        self.assertEqual(result["findings"][0]["label"], "BLOCKING")


class VerdictGateTests(unittest.TestCase):
    def test_approve_with_active_p1_is_rejected(self):
        with self.assertRaisesRegex(TraceError, "APPROVE requires COMPLETE coverage and no active P1/BLOCKING"):
            validate_review_packet(
                raw_packet(
                    findings=[legacy_finding("F-1", severity="P1", label="BLOCKING", disposition="OPEN")],
                    verdict="APPROVE",
                    artifact_sha=sha(11),
                )
            )

    def test_approve_with_incomplete_coverage_is_rejected(self):
        with self.assertRaisesRegex(TraceError, "APPROVE requires COMPLETE coverage and no active P1/BLOCKING"):
            validate_review_packet(
                raw_packet(verdict="APPROVE", coverage="PARTIAL", unreviewed=["docs"], artifact_sha=sha(11))
            )

    def test_approve_without_independent_artifact_is_rejected(self):
        with self.assertRaisesRegex(TraceError, "APPROVE requires independent artifact"):
            validate_review_packet(raw_packet(verdict="APPROVE"))

    def test_request_changes_without_active_blocker_is_rejected(self):
        with self.assertRaisesRegex(TraceError, "REQUEST_CHANGES requires an active P1/BLOCKING"):
            validate_review_packet(
                raw_packet(
                    findings=[legacy_finding("F-1", severity="P2", label="FOLLOW_UP", disposition="FOLLOW_UP")],
                    verdict="REQUEST_CHANGES",
                )
            )

    def test_author_packet_with_non_null_verdict_is_rejected(self):
        packet = raw_packet(
            findings=[legacy_finding("F-1", severity="P1", label="BLOCKING", disposition="OPEN")],
            verdict="REQUEST_CHANGES",
        )
        with self.assertRaisesRegex(TraceError, "author packet must have null verdict"):
            ingest_independent_artifact(
                packet,
                {},
                expected_dispatch_transcript_sha256=LINEAGE["dispatch_transcript_sha256"],
                expected_task_id=LINEAGE["task_id"],
                expected_parent_task_id=LINEAGE["parent_task_id"],
                expected_sender=LINEAGE["sender"],
            )


class FirstRoundClosureTests(unittest.TestCase):
    def test_initial_clean_room_review_cannot_mark_findings_fixed(self):
        packet = trace_packet()
        finding = full_finding("R-1", disposition="FIXED")
        artifact = formal_artifact(packet, verdict="APPROVE", findings=[finding])
        with self.assertRaisesRegex(TraceError, "initial review cannot mark findings FIXED"):
            ingest(packet, artifact)


class EscalationDriftTests(unittest.TestCase):
    def _escalated(self, *, reviewed_scope):
        prior_packet = trace_packet()
        prior = formal_artifact(prior_packet, run_id="path-prior")
        basis = copy.deepcopy(prior_packet["decision_basis"])
        route, model, effort = route_for("high")
        basis.update(
            {
                "review_risk": "high",
                "reviewer_route": route,
                "reviewer_model": model,
                "reasoning_effort": effort,
                "high_risk_triggers": ["security"],
            }
        )
        basis["review_policy_sha256"] = canonical_sha256(
            {
                "required_stages": basis["required_stages"],
                "review_risk": "high",
                "reviewer_route": route,
                "reviewer_model": model,
                "reasoning_effort": effort,
                "classifier_identity": basis["classifier_identity"],
                "high_risk_triggers": basis["high_risk_triggers"],
            }
        )
        packet = trace_packet(head="d" * 40, tree="e" * 40, basis=basis, reviewed_scope=reviewed_scope)
        lineage = {**LINEAGE, "task_id": "/root/path-review", "sender": "/root/path-review"}
        artifact = formal_artifact(
            packet,
            mode="escalated_fresh",
            prior=prior,
            continuity="path-reviewer",
            run_id="path-run",
            lineage=lineage,
            trigger="PATH_SET_SCOPE_DRIFT",
        )
        return packet, prior, artifact, lineage

    def test_escalation_trigger_with_matching_drift_is_accepted(self):
        packet, prior, artifact, lineage = self._escalated(reviewed_scope=["gov/zgov", "tests"])
        approved = ingest(
            packet,
            artifact,
            prior_artifact=prior,
            expected_escalation_trigger="PATH_SET_SCOPE_DRIFT",
            expected_distinct_reviewer_task_id=lineage["task_id"],
            expected_prior_reviewer_task_id=LINEAGE["task_id"],
        )
        self.assertEqual(approved["verdict"], "APPROVE")

    def test_escalation_trigger_without_matching_drift_is_rejected(self):
        packet, prior, artifact, lineage = self._escalated(reviewed_scope=["gov/zgov"])
        with self.assertRaisesRegex(TraceError, "escalation trigger has no matching old/new drift"):
            ingest(
                packet,
                artifact,
                prior_artifact=prior,
                expected_escalation_trigger="PATH_SET_SCOPE_DRIFT",
                expected_distinct_reviewer_task_id=lineage["task_id"],
                expected_prior_reviewer_task_id=LINEAGE["task_id"],
            )


if __name__ == "__main__":
    unittest.main()
