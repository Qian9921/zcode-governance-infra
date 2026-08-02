"""Evidence count arithmetic negative/positive cases for zgov."""
from __future__ import annotations

import unittest

from zgov.evidence import EvidenceError, validate_counts, validate_row


def _green_row(**overrides) -> dict:
    base = {
        "schema": "evidence-row.v16",
        "case_id": "EVID-G-TARGETED-EP-UNIT",
        "semantics": "unit",
        "gate_id": "G-TARGETED",
        "stage": "targeted",
        "decision": "allow",
        "expected_head": "e18439c8dfe01d901895efd09b8b73b6842327a9",
        "actual_head": "e18439c8dfe01d901895efd09b8b73b6842327a9",
        "tree_sha": "1de79a7c48e6c66f167be54ca9cf387310149f80",
        "identity_mode": "git-exact-object",
        "snapshot_sha256": "",
        "dirty": False,
        "command": ["python3", "-c", "print()"],
        "cwd": ".",
        "runtime": "python3",
        "config": "default",
        "started_at": "2024-01-01T00:00:00Z",
        "ended_at": "2024-01-01T00:00:01Z",
        "elapsed_sec": 1,
        "exit_status": 0,
        "counts": {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0},
        "log_sha256": "0" * 64,
        "log_mode": 0o644,
        "log_size": 1,
        "reused": False,
        "superseded": False,
    }
    base.update(overrides)
    return base


class EvidenceCountsTests(unittest.TestCase):

    def test_total_arithmetic_mismatch(self):
        counts = {"total": 5, "ran": 2, "passed": 2, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}
        with self.assertRaisesRegex(EvidenceError, r"total must equal passed\+failed\+skipped\+unknown"):
            validate_counts(counts)

    def test_ran_arithmetic_mismatch(self):
        counts = {"total": 3, "ran": 2, "passed": 1, "failed": 2, "skipped": 0, "xfail": 0, "unknown": 0}
        with self.assertRaisesRegex(EvidenceError, r"ran must equal passed\+failed"):
            validate_counts(counts)

    def test_zero_denominator_known_rejected(self):
        counts = {"total": 0, "ran": 0, "passed": 0, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}
        with self.assertRaisesRegex(EvidenceError, "known denominator must be > 0"):
            validate_counts(counts)

    def test_skipped_cannot_be_green(self):
        counts = {"total": 1, "ran": 0, "passed": 0, "failed": 0, "skipped": 1, "xfail": 0, "unknown": 0}
        with self.assertRaisesRegex(EvidenceError, "green evidence requires"):
            validate_counts(counts)

    def test_bool_value_rejected_as_integer(self):
        counts = {"total": True, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}
        with self.assertRaisesRegex(EvidenceError, "bool is not an integer"):
            validate_counts(counts)

    def test_deny_exit_zero_ambiguous(self):
        row = _green_row(decision="deny", exit_status=0)
        with self.assertRaisesRegex(EvidenceError, "deny with exit 0 is ambiguous"):
            validate_row(row, require_green=False)

    def test_baseline_row_is_accepted(self):
        # Control for the negative cases above: the unmodified row must pass,
        # proving each negative poked exactly one hole.
        self.assertEqual(validate_row(_green_row(), require_green=False)["decision"], "allow")

    def test_valid_green_counts(self):
        counts = {"total": 1, "ran": 1, "passed": 1, "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0}
        result = validate_counts(counts)
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["unknown"], 0)


if __name__ == "__main__":
    unittest.main()
