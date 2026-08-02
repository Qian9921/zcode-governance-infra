"""Milestone parameterization and fail-closed behaviour for zgov.spark."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from zgov import milestone, roles, spark


def _unfrozen_env() -> dict[str, str]:
    """Point ZGOV_MILESTONE_PATH at a path that does not exist."""
    return {"ZGOV_MILESTONE_PATH": "/nonexistent/zgov-milestone-does-not-exist.json"}


class _EnvMixin(unittest.TestCase):
    def _set_milestone_path(self, value: str) -> None:
        previous = os.environ.get("ZGOV_MILESTONE_PATH")
        os.environ["ZGOV_MILESTONE_PATH"] = value

        def restore() -> None:
            if previous is None:
                os.environ.pop("ZGOV_MILESTONE_PATH", None)
            else:
                os.environ["ZGOV_MILESTONE_PATH"] = previous

        self.addCleanup(restore)


class UnfrozenFailClosed(_EnvMixin):
    """Trust-chain validators must refuse to run without a frozen baseline."""

    def setUp(self) -> None:
        self._set_milestone_path("/nonexistent/zgov-milestone-does-not-exist.json")
        self.assertFalse(milestone.is_frozen())

    def test_dispatch_transcript_raises_milestone_not_frozen(self) -> None:
        with self.assertRaises(milestone.MilestoneNotFrozen) as ctx:
            spark.validate_dispatch_transcript({"schema": "dispatch-transcript.v16"})
        self.assertIn("MILESTONE_NOT_FROZEN", str(ctx.exception))

    def test_author_closure_raises_milestone_not_frozen(self) -> None:
        value = {
            "schema": "author-closure-final.v16",
            "mission_id": "V16-PRODUCTIVITY",
            "candidate_head_sha": "a" * 40,
            "candidate_tree_sha": "b" * 40,
            "audited_head_sha": "c" * 40,
            "plan_sha256": "d" * 64,
            "finding_count": 1,
            "findings": [],
            "disposition_summary": {"FIXED": 1, "DISAGREE": 0, "FOLLOW_UP": 0},
            "artifact_sha256": "e" * 64,
        }
        with self.assertRaises(milestone.MilestoneNotFrozen) as ctx:
            spark.validate_author_closure(value)
        self.assertIn("MILESTONE_NOT_FROZEN", str(ctx.exception))

    def test_closure_binding_receipt_raises_milestone_not_frozen(self) -> None:
        with self.assertRaises(milestone.MilestoneNotFrozen) as ctx:
            spark.build_closure_binding_receipt(
                {},
                compiled_plan={"schema": "compiled-plan.v16"},
                spark_requests=[],
                spark_results=[],
                closure_plan_file_sha256="f" * 64,
                dispatch_transcript_file_sha256="0" * 64,
                normalized_source_artifact_paths={},
            )
        self.assertIn("MILESTONE_NOT_FROZEN", str(ctx.exception))

    def test_author_closure_plan_raises_milestone_not_frozen(self) -> None:
        value = {
            "schema": "author-closure-plan.v16",
            "mission_id": "V16-PRODUCTIVITY",
            "audited_head_sha": "c" * 40,
            "finding_count": 0,
            "findings": [],
            "candidate_binding": "external_evidence_envelope",
            "plan_sha256": "d" * 64,
        }
        with self.assertRaises(milestone.MilestoneNotFrozen) as ctx:
            spark.validate_author_closure_plan(value)
        self.assertIn("MILESTONE_NOT_FROZEN", str(ctx.exception))

    def test_structural_validators_still_work_unfrozen(self) -> None:
        """Positive case: pure structural validation needs no frozen baseline."""
        request = {
            "schema": "spark-audit-request.v16",
            "audit_id": "AUDIT-1",
            "mission_id": "M-1",
            "domain": "orchestration",
            "scope": ["orchestration"],
            "max_findings": 4,
            "assigned_model": spark._spark_model(),
            "role": "inner-auditor",
            "permissions": ["read"],
            "fork_turns": "none",
            "context_mode": "zero-context",
            "report_only": True,
            "spawn_index": 1,
        }
        checked = spark.validate_request(request)
        self.assertEqual(checked["audit_id"], "AUDIT-1")
        self.assertEqual(checked["spawn_index"], 1)

    def test_audit_requests_still_work_unfrozen(self) -> None:
        mission = {
            "mission_id": "M-1",
            "spark_audits": [
                {"id": "AUDIT-1", "domain": "orchestration", "scope": ["orchestration"], "max_findings": 4},
            ],
        }
        requests = spark.audit_requests(mission)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["audit_id"], "AUDIT-1")


def _request(audit_id: str, index: int) -> dict:
    return {
        "schema": "spark-audit-request.v16",
        "audit_id": audit_id,
        "mission_id": "M-1",
        "domain": "orchestration",
        "scope": ["orchestration"],
        "max_findings": 4,
        "assigned_model": spark._spark_model(),
        "role": "inner-auditor",
        "permissions": ["read"],
        "fork_turns": "none",
        "context_mode": "zero-context",
        "report_only": True,
        "spawn_index": index,
    }


def _result(audit_id: str) -> dict:
    return {
        "schema": "spark-audit-result.v16",
        "audit_id": audit_id,
        "mission_id": "M-1",
        "task_id": "/root/task",
        "assigned_model": spark._spark_model(),
        "reasoning_effort": roles.effort_for("auditor_spark"),
        "fork_turns": "none",
        "context_mode": "zero-context",
        "report_only": True,
        "scope": "orchestration",
        "findings": [],
        "dispositions": {},
        "started_at": "2026-01-01T00:00:00Z",
        "ended_at": "2026-01-01T00:00:10Z",
        "elapsed_sec": 10.0,
    }


class SparkBudgetAndBundle(_EnvMixin):
    """Budget ceiling and exact request/result set matching."""

    def setUp(self) -> None:
        self._set_milestone_path("/nonexistent/zgov-milestone-does-not-exist.json")

    def test_four_audits_rejected(self) -> None:
        requests = [_request(f"AUDIT-{i}", i) for i in range(1, 5)]
        results = [_result(f"AUDIT-{i}") for i in range(1, 5)]
        with self.assertRaises(spark.SparkAuditError):
            spark.validate_bundle(requests, results)

    def test_budget_above_three_rejected(self) -> None:
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_bundle([], [], budget=4)
        self.assertIn("Spark spawn budget must be in [0,3]", str(ctx.exception))

    def test_mission_with_four_audits_rejected(self) -> None:
        mission = {
            "mission_id": "M-1",
            "spark_audits": [
                {"id": f"AUDIT-{i}", "domain": "d", "scope": ["orchestration"], "max_findings": 4}
                for i in range(1, 5)
            ],
        }
        with self.assertRaises(spark.SparkAuditError):
            spark.audit_requests(mission)

    def test_missing_result_rejected(self) -> None:
        requests = [_request("AUDIT-1", 1), _request("AUDIT-2", 2)]
        results = [_result("AUDIT-1")]
        with self.assertRaises(spark.SparkAuditError):
            spark.validate_bundle(requests, results)

    def test_extra_result_rejected(self) -> None:
        requests = [_request("AUDIT-1", 1)]
        results = [_result("AUDIT-1"), _result("AUDIT-2")]
        with self.assertRaises(spark.SparkAuditError):
            spark.validate_bundle(requests, results)

    def test_duplicate_result_rejected(self) -> None:
        requests = [_request("AUDIT-1", 1), _request("AUDIT-2", 2)]
        results = [_result("AUDIT-1"), _result("AUDIT-1")]
        with self.assertRaises(spark.SparkAuditError):
            spark.validate_bundle(requests, results)

    def test_matching_bundle_accepted(self) -> None:
        requests = [_request("AUDIT-1", 1), _request("AUDIT-2", 2)]
        results = [_result("AUDIT-1"), _result("AUDIT-2")]
        checked = spark.validate_bundle(requests, results)
        self.assertEqual({r["audit_id"] for r in checked}, {"AUDIT-1", "AUDIT-2"})


def _frozen_milestone_document() -> dict:
    audit_ids = ["RE-AUDIT-A", "RE-AUDIT-B"]
    normalized = {"RE-AUDIT-A": ["A-1", "A-2"], "RE-AUDIT-B": ["B-1"]}
    author_ids = normalized["RE-AUDIT-A"] + normalized["RE-AUDIT-B"]
    return {
        "schema": milestone.MILESTONE_SCHEMA,
        "frozen": True,
        "milestone_id": "ZGOV-TEST",
        "repo": "zcode-governance-infra",
        "base_sha": "1" * 40,
        "base_tree": "2" * 40,
        "spark": {
            "audit_ids": audit_ids,
            "expected_raw_platform_sha256": {"RE-AUDIT-A": "a" * 64, "RE-AUDIT-B": "b" * 64},
            "normalized_finding_ids": normalized,
            "author_closure_denominator": len(author_ids),
            "author_finding_ids": author_ids,
            "historical_findings": {"H-1": "HISTORICAL_UNVERIFIED"},
            "expected_historical": [],
            "expected_current": [],
        },
        "gate_stage_map": {"G-TARGETED": "targeted", "G-FULL": "full", "G-FRESH": "fresh"},
    }


class FrozenMilestonePath(_EnvMixin):
    """With a frozen milestone the trust-chain validators run for real."""

    def setUp(self) -> None:
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        path = Path(tmpdir) / "milestone.json"
        path.write_text(json.dumps(_frozen_milestone_document()), encoding="utf-8")
        self._set_milestone_path(str(path))

    def test_milestone_is_frozen(self) -> None:
        self.assertTrue(milestone.is_frozen())

    def test_spark_config_is_externalized(self) -> None:
        cfg = milestone.spark_config()
        self.assertEqual(cfg["expected_raw_platform_sha256"]["RE-AUDIT-A"], "a" * 64)
        self.assertEqual(cfg["normalized_finding_ids"]["RE-AUDIT-B"], ["B-1"])
        self.assertEqual(spark.author_closure_denominator(), 3)
        self.assertEqual(spark.author_finding_ids(), ("A-1", "A-2", "B-1"))
        self.assertEqual(milestone.base_identity(), ("1" * 40, "2" * 40))

    def test_dispatch_transcript_no_longer_not_frozen(self) -> None:
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_dispatch_transcript({"schema": "dispatch-transcript.v16"})
        self.assertNotIsInstance(ctx.exception, milestone.MilestoneNotFrozen)

    def test_author_closure_no_longer_not_frozen(self) -> None:
        value = {
            "schema": "author-closure-final.v16",
            "mission_id": "V16-PRODUCTIVITY",
            "candidate_head_sha": "a" * 40,
            "candidate_tree_sha": "b" * 40,
            "audited_head_sha": "c" * 40,
            "plan_sha256": "d" * 64,
            "finding_count": 99,
            "findings": [],
            "disposition_summary": {"FIXED": 1, "DISAGREE": 0, "FOLLOW_UP": 0},
            "artifact_sha256": "e" * 64,
        }
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_author_closure(value)
        self.assertNotIsInstance(ctx.exception, milestone.MilestoneNotFrozen)
        self.assertIn("author closure denominator", str(ctx.exception))

    def test_author_closure_plan_no_longer_not_frozen(self) -> None:
        value = {
            "schema": "author-closure-plan.v16",
            "mission_id": "V16-PRODUCTIVITY",
            "audited_head_sha": "c" * 40,
            "finding_count": 99,
            "findings": [],
            "candidate_binding": "external_evidence_envelope",
            "plan_sha256": "d" * 64,
        }
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_author_closure_plan(value)
        self.assertNotIsInstance(ctx.exception, milestone.MilestoneNotFrozen)

    def test_closure_binding_receipt_no_longer_not_frozen(self) -> None:
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.build_closure_binding_receipt(
                {},
                compiled_plan={"schema": "compiled-plan.v16"},
                spark_requests=[],
                spark_results=[],
                closure_plan_file_sha256="f" * 64,
                dispatch_transcript_file_sha256="0" * 64,
                normalized_source_artifact_paths={},
            )
        self.assertNotIsInstance(ctx.exception, milestone.MilestoneNotFrozen)


class NoDiskReadAtImport(unittest.TestCase):
    """Importing zgov.spark must not read the milestone file."""

    def test_import_with_missing_path_is_clean(self) -> None:
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["ZGOV_MILESTONE_PATH"] = "/nonexistent/zgov-milestone-does-not-exist.json"
        env["PYTHONPATH"] = str(repo_root / "gov")
        proc = subprocess.run(
            [sys.executable, "-c", "import zgov.spark; from zgov import milestone; print(milestone.is_frozen())"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(repo_root),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
