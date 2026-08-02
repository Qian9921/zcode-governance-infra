"""Milestone ``transcript`` carrier fields and the spark pins they re-enable.

The dispatch-transcript identity pins (mission scope, compiled plan, audited
parent, author closure plan) were value-asserted against hardcoded literals
upstream.  Porting them required a carrier in the milestone config; without it
the checks degrade to shape-only (``_hash64``), which accepts *any* 64-hex
string.  These tests lock both halves: the carrier validates, and a configured
pin actually rejects a mismatching transcript.
"""
from __future__ import annotations

import copy
import unittest

from zgov import milestone, spark

_BASE_SHA = "a" * 40
_BASE_TREE = "b" * 40
_AUDITED_PARENT = "c" * 40

_MISSION_SCOPE = "1" * 64
_COMPILED_PLAN = "2" * 64
_CLOSURE_PLAN = "3" * 64

_WRONG_64 = "9" * 64


def _milestone_doc(*, frozen: bool = True, transcript: dict | None = None) -> dict:
    """A minimal milestone document accepted by ``validate_milestone``."""
    doc = {
        "schema": milestone.MILESTONE_SCHEMA,
        "frozen": frozen,
        "milestone_id": "M-CARRIER-TEST" if frozen else None,
        "repo": "example/repo" if frozen else None,
        "base_sha": _BASE_SHA if frozen else None,
        "base_tree": _BASE_TREE if frozen else None,
        "spark": {
            "audit_ids": [],
            "expected_raw_platform_sha256": {},
            "normalized_finding_ids": {},
            "author_closure_denominator": None,
            "author_finding_ids": [],
            "historical_findings": {},
            "expected_historical": {},
            "expected_current": {},
        },
        "gate_stage_map": {"G-TARGETED": "targeted", "G-FULL": "full", "G-FRESH": "fresh"},
    }
    if transcript is not None:
        doc["transcript"] = transcript
    return doc


def _full_transcript_pins() -> dict:
    return {
        "mission_scope_sha256": _MISSION_SCOPE,
        "compiled_plan_sha256": _COMPILED_PLAN,
        "audited_parent_sha": _AUDITED_PARENT,
        "author_closure_plan_sha256": _CLOSURE_PLAN,
    }


class TranscriptCarrierValidation(unittest.TestCase):
    """``validate_milestone`` must carry and strictly check the four pins."""

    def test_missing_transcript_key_defaults_to_four_nulls(self) -> None:
        """Backward compatibility: pre-existing configs have no transcript key."""
        doc = _milestone_doc(frozen=False)
        self.assertNotIn("transcript", doc)
        cfg = milestone.validate_milestone(doc)
        self.assertEqual(
            cfg["transcript"],
            {
                "mission_scope_sha256": None,
                "compiled_plan_sha256": None,
                "audited_parent_sha": None,
                "author_closure_plan_sha256": None,
            },
        )
        self.assertEqual(
            milestone.transcript_config(cfg),
            {name: None for name in milestone.TRANSCRIPT_FIELDS},
        )

    def test_unknown_transcript_key_is_rejected(self) -> None:
        pins = _full_transcript_pins()
        pins["surprise_sha256"] = "d" * 64
        with self.assertRaises(milestone.MilestoneError) as ctx:
            milestone.validate_milestone(_milestone_doc(transcript=pins))
        self.assertIn("surprise_sha256", str(ctx.exception))
        self.assertIn("additional property not allowed", str(ctx.exception))

    def test_mission_scope_sha256_of_63_hex_is_rejected(self) -> None:
        pins = _full_transcript_pins()
        pins["mission_scope_sha256"] = "1" * 63
        with self.assertRaises(milestone.MilestoneError) as ctx:
            milestone.validate_milestone(_milestone_doc(transcript=pins))
        self.assertIn("transcript.mission_scope_sha256", str(ctx.exception))
        self.assertIn("64-character lowercase hex", str(ctx.exception))

    def test_audited_parent_sha_of_64_hex_is_rejected(self) -> None:
        """``audited_parent_sha`` is a 40-hex Git SHA, not a 64-hex digest."""
        pins = _full_transcript_pins()
        pins["audited_parent_sha"] = "c" * 64
        with self.assertRaises(milestone.MilestoneError) as ctx:
            milestone.validate_milestone(_milestone_doc(transcript=pins))
        self.assertIn("transcript.audited_parent_sha", str(ctx.exception))
        self.assertIn("40-character lowercase hex", str(ctx.exception))

    def test_all_four_valid_values_round_trip(self) -> None:
        cfg = milestone.validate_milestone(_milestone_doc(transcript=_full_transcript_pins()))
        self.assertEqual(cfg["transcript"], _full_transcript_pins())
        pins = milestone.transcript_config(cfg)
        self.assertEqual(pins["mission_scope_sha256"], _MISSION_SCOPE)
        self.assertEqual(pins["compiled_plan_sha256"], _COMPILED_PLAN)
        self.assertEqual(pins["audited_parent_sha"], _AUDITED_PARENT)
        self.assertEqual(pins["author_closure_plan_sha256"], _CLOSURE_PLAN)

    def test_frozen_milestone_does_not_require_the_pins(self) -> None:
        """Not every frozen milestone has a dispatch transcript."""
        nulls = {name: None for name in milestone.TRANSCRIPT_FIELDS}
        cfg = milestone.validate_milestone(_milestone_doc(frozen=True, transcript=nulls))
        self.assertTrue(cfg["frozen"])
        self.assertEqual(cfg["transcript"], nulls)

    def test_transcript_must_be_an_object(self) -> None:
        with self.assertRaises(milestone.MilestoneError) as ctx:
            milestone.validate_milestone(_milestone_doc(transcript=["not", "an", "object"]))
        self.assertIn("transcript: object required", str(ctx.exception))


def _transcript_doc() -> dict:
    """A transcript valid up to (and including) the identity-pin checks.

    Fields after the pin checks are intentionally placeholders: the pin
    assertions must fire before any of them is inspected.
    """
    return {
        "schema": "dispatch-transcript.v16",
        "transcript_version": 2,
        "lineage_mode": "DISPATCH_TRANSCRIPT",
        "mission_id": "M-CARRIER-TEST",
        "base_sha": _BASE_SHA,
        "base_tree": _BASE_TREE,
        "mission_scope_sha256": _MISSION_SCOPE,
        "compiled_plan_sha256": _COMPILED_PLAN,
        "reviewed_head_sha": _AUDITED_PARENT,
        "reviewed_tree_sha": "d" * 40,
        "snapshot": {},
        "audited_input_snapshot": {},
        "candidate_binding": {},
        "historical_original_audits": [],
        "accepted_current_audits": [],
        "finding_dispositions": {},
        "ordering": {},
        "historical_spawn_count": 6,
        "accepted_current_spawn_count": 3,
        "transcript_sha256": "e" * 64,
    }


class DispatchTranscriptPinsAreEnforced(unittest.TestCase):
    """A configured pin must reject a mismatching transcript by value."""

    def test_mission_scope_mismatch_raises_spark_audit_error(self) -> None:
        cfg = milestone.validate_milestone(_milestone_doc(transcript=_full_transcript_pins()))
        doc = _transcript_doc()
        doc["mission_scope_sha256"] = _WRONG_64
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_dispatch_transcript(doc, milestone_cfg=cfg)
        self.assertIn("mission scope/compiled plan identity changed", str(ctx.exception))
        # The failure must be the value assertion, not a missing trust root.
        self.assertNotIsInstance(ctx.exception, milestone.MilestoneNotFrozen)

    def test_compiled_plan_mismatch_raises_spark_audit_error(self) -> None:
        cfg = milestone.validate_milestone(_milestone_doc(transcript=_full_transcript_pins()))
        doc = _transcript_doc()
        doc["compiled_plan_sha256"] = _WRONG_64
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_dispatch_transcript(doc, milestone_cfg=cfg)
        self.assertIn("mission scope/compiled plan identity changed", str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, milestone.MilestoneNotFrozen)

    def test_null_pins_do_not_assert_values(self) -> None:
        """With no pin configured the same input must not fail *on value*."""
        nulls = {name: None for name in milestone.TRANSCRIPT_FIELDS}
        cfg = milestone.validate_milestone(_milestone_doc(transcript=nulls))
        doc = _transcript_doc()
        doc["mission_scope_sha256"] = _WRONG_64
        doc["compiled_plan_sha256"] = _WRONG_64
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_dispatch_transcript(doc, milestone_cfg=cfg)
        # It still fails (placeholder fields downstream), but never on identity.
        self.assertNotIn("mission scope/compiled plan identity changed", str(ctx.exception))

    def test_pinned_values_that_match_pass_the_identity_check(self) -> None:
        """The matching case must get *past* the pin, proving it is not a no-op."""
        cfg = milestone.validate_milestone(_milestone_doc(transcript=_full_transcript_pins()))
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_dispatch_transcript(_transcript_doc(), milestone_cfg=cfg)
        self.assertNotIn("mission scope/compiled plan identity changed", str(ctx.exception))

    def test_audited_parent_mismatch_raises_spark_audit_error(self) -> None:
        cfg = milestone.validate_milestone(_milestone_doc(transcript=_full_transcript_pins()))
        doc = _transcript_doc()
        # Reach the audited-parent pin with a well-formed audited snapshot.
        doc["audited_input_snapshot"] = {
            "identity_mode": "git-exact-object",
            "head_sha": "f" * 40,
            "tree_sha": "d" * 40,
            "path_hash_set_sha256": "4" * 64,
        }
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_dispatch_transcript(doc, milestone_cfg=cfg)
        self.assertIn("audited remediation parent identity changed", str(ctx.exception))

    def test_audited_parent_pin_is_skipped_when_null(self) -> None:
        pins = _full_transcript_pins()
        pins["audited_parent_sha"] = None
        cfg = milestone.validate_milestone(_milestone_doc(transcript=pins))
        doc = _transcript_doc()
        doc["audited_input_snapshot"] = {
            "identity_mode": "git-exact-object",
            "head_sha": "f" * 40,
            "tree_sha": "d" * 40,
            "path_hash_set_sha256": "4" * 64,
        }
        with self.assertRaises(spark.SparkAuditError) as ctx:
            spark.validate_dispatch_transcript(doc, milestone_cfg=cfg)
        self.assertNotIn("audited remediation parent identity changed", str(ctx.exception))


class ExampleMilestoneStillValidates(unittest.TestCase):
    """The shipped example config must survive the schema extension."""

    def test_example_json_has_transcript_and_validates(self) -> None:
        import json
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[2] / "gov" / "milestone.example.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("transcript", doc)
        cfg = milestone.validate_milestone(copy.deepcopy(doc))
        self.assertEqual(cfg["transcript"], {name: None for name in milestone.TRANSCRIPT_FIELDS})


if __name__ == "__main__":
    unittest.main()
