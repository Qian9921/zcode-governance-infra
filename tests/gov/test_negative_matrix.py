"""Executable checks for the twenty-eight-family R1 negative matrix.

The matrix is the executable specification of the gate contract: each family
feeds a deliberately malformed candidate to an ordinary validator and records
whether that candidate was rejected.  A family row is ``GREEN`` when the
malformed candidate was rejected, ``RED`` when it was accepted (the gate
failed) or when the probe could not run at all, and
``SKIPPED_MILESTONE_NOT_FROZEN`` when the mechanism is only observable against
frozen milestone facts.

Every assertion here derives model identities from the *live* roles
configuration; no model name is written down.  The two concrete test classes
run the whole matrix under placeholder and under fully resolved roles.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import support

import zgov
from zgov import r1, roles
from zgov.review_policy import resolve_reviewer

PACKAGE_ROOT = pathlib.Path(zgov.__file__).resolve().parent

# ``run_negative_matrix`` resolves the mission fixture relative to the governed
# repository root, matching the ``zgov/v16/...`` convention already used by
# ``zgov.spark``.
MISSION_FIXTURE_REL = "zgov/v16/fixtures/mission.valid.json"

# Families whose probe cannot execute in this repository because the artifact
# it inspects has not been ported yet.  Pinning the set keeps the denominator
# of actually-exercised families known (28 - 3 skipped - 1 unavailable = 24).
UNAVAILABLE_CASE_IDS = frozenset({"NF-020"})  # needs scripts/verify-governance.py + manifest.json


def _build_probe_root(testcase: unittest.TestCase) -> pathlib.Path:
    """Create a clean, committed Git root carrying the mission fixture."""
    tmpdir = tempfile.TemporaryDirectory(prefix="r1-negative-matrix-")
    testcase.addCleanup(tmpdir.cleanup)
    root = pathlib.Path(tmpdir.name) / "repo"
    fixture = root / MISSION_FIXTURE_REL
    fixture.parent.mkdir(parents=True)
    shutil.copy(PACKAGE_ROOT / "fixtures" / "mission.valid.json", fixture)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "r1-matrix@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "R1 Negative Matrix"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "r1 negative matrix probe root"], cwd=root, check=True)
    return root


def _pin_unfrozen_milestone(testcase: unittest.TestCase) -> None:
    """Point ``ZGOV_MILESTONE_PATH`` at an absent file for one test."""
    tmpdir = tempfile.TemporaryDirectory(prefix="r1-milestone-")
    testcase.addCleanup(tmpdir.cleanup)
    previous = os.environ.get("ZGOV_MILESTONE_PATH")

    def restore() -> None:
        if previous is None:
            os.environ.pop("ZGOV_MILESTONE_PATH", None)
        else:
            os.environ["ZGOV_MILESTONE_PATH"] = previous

    testcase.addCleanup(restore)
    os.environ["ZGOV_MILESTONE_PATH"] = str(pathlib.Path(tmpdir.name) / "absent-milestone.json")


class NegativeMatrixIdentityTest(unittest.TestCase):
    """Static shape of the frozen matrix, independent of any execution."""

    def test_exactly_twenty_eight_stable_families(self) -> None:
        self.assertEqual(len(r1.NEGATIVE_FAMILIES), 28)
        case_ids = [family["case_id"] for family in r1.NEGATIVE_FAMILIES]
        self.assertEqual(case_ids, [f"NF-{index:03d}" for index in range(1, 29)])
        families = [family["family"] for family in r1.NEGATIVE_FAMILIES]
        self.assertEqual(len(set(families)), 28)
        for family in r1.NEGATIVE_FAMILIES:
            self.assertEqual(set(family), {"case_id", "family", "mechanism"})
            self.assertTrue(family["mechanism"])

    def test_eleven_stable_finding_ids(self) -> None:
        self.assertEqual(
            list(r1.R1_FINDINGS), [f"V16-R1-{index:03d}" for index in range(1, 12)]
        )

    def test_milestone_dependent_families_are_declared(self) -> None:
        declared = set(r1.MILESTONE_DEPENDENT_FAMILIES)
        known = {family["family"] for family in r1.NEGATIVE_FAMILIES}
        self.assertTrue(declared)
        self.assertLessEqual(declared, known)


class _NegativeMatrixContract:
    """Full-matrix contract, re-run under one roles configuration."""

    RESOLVED_ROLES: bool

    def setUp(self) -> None:  # type: ignore[override]
        support.pinned_roles(self, resolved=self.RESOLVED_ROLES)
        _pin_unfrozen_milestone(self)
        self.root = _build_probe_root(self)

    def test_reviewer_identity_tracks_the_live_roles_config(self) -> None:
        """Prove the two classes really exercise different role resolutions."""
        mission = json.loads((self.root / MISSION_FIXTURE_REL).read_text(encoding="utf-8"))
        pinned = support.mission_with_current_reviewer(mission)
        risk = support.mission_review_risk(mission)
        model = resolve_reviewer(risk)["model"]
        self.assertEqual(pinned["reviewer_separation"]["independent_model"], model)
        self.assertEqual(roles.is_placeholder(model), not self.RESOLVED_ROLES)

    def test_negative_matrix_denominator_and_family_outcomes(self) -> None:
        result = r1.run_negative_matrix(self.root)

        self.assertEqual(result["schema"], "negative-matrix.v16")
        self.assertEqual(result["matrix_id"], "V16-R1-NEGATIVE-FAMILIES")
        self.assertTrue(result["matrix_sha256"])

        rows = result["rows"]
        self.assertEqual(len(rows), 28)
        self.assertEqual(result["total"], 28)
        self.assertEqual(
            [row["case_id"] for row in rows],
            [family["case_id"] for family in r1.NEGATIVE_FAMILIES],
        )
        # total = passed + failed + skipped, with a per-row denominator of one.
        self.assertEqual(
            result["total"], result["passed"] + result["failed"] + result["skipped"]
        )
        self.assertEqual(result["xfail"], 0)
        self.assertEqual(result["unknown"], 0)

        green: list[str] = []
        red: list[str] = []
        skipped: list[str] = []
        for row in rows:
            with self.subTest(case_id=row["case_id"]):
                self.assertEqual(row["expected"], "RED")
                self.assertEqual(row["denominator"], 1)
                self.assertEqual(row["total"], 1)
                self.assertTrue(row["row_sha256"])
                if row["status"] == "GREEN":
                    self.assertEqual(row["error"], "")
                    self.assertEqual((row["ran"], row["passed"], row["failed"], row["skipped"]), (1, 1, 0, 0))
                    green.append(row["case_id"])
                elif row["status"] == r1.SKIPPED_MILESTONE_NOT_FROZEN:
                    self.assertIn(row["family"], r1.MILESTONE_DEPENDENT_FAMILIES)
                    self.assertEqual((row["ran"], row["passed"], row["failed"], row["skipped"]), (0, 0, 0, 1))
                    skipped.append(row["case_id"])
                else:
                    self.assertEqual(row["status"], "RED")
                    # The one failure mode that must never appear: the gate
                    # accepted the malformed candidate.
                    self.assertNotEqual(
                        row["error"],
                        "negative candidate was accepted",
                        f"{row['case_id']} ({row['family']}): malformed candidate was ACCEPTED",
                    )
                    self.assertEqual((row["ran"], row["passed"], row["failed"], row["skipped"]), (1, 0, 1, 0))
                    red.append(row["case_id"])

        self.assertEqual(len(green) + len(red) + len(skipped), 28)
        # A milestone-dependent family must never report an unearned GREEN:
        # its validators reject everything while the baseline is unfrozen.
        self.assertEqual(
            set(skipped),
            {
                family["case_id"]
                for family in r1.NEGATIVE_FAMILIES
                if family["family"] in r1.MILESTONE_DEPENDENT_FAMILIES
            },
        )
        self.assertEqual(set(red), set(UNAVAILABLE_CASE_IDS))
        self.assertEqual(len(green), 28 - len(skipped) - len(UNAVAILABLE_CASE_IDS))
        # An incomplete denominator can never present as an overall pass.
        self.assertNotEqual(result["status"], "GREEN")
        self.assertEqual(result["status"], "RED")


class NegativeMatrixPlaceholderRolesTest(_NegativeMatrixContract, unittest.TestCase):
    RESOLVED_ROLES = False


class NegativeMatrixResolvedRolesTest(_NegativeMatrixContract, unittest.TestCase):
    RESOLVED_ROLES = True


class VerifyCandidateFindingTest(unittest.TestCase):
    def setUp(self) -> None:
        support.pinned_roles(self, resolved=True)
        _pin_unfrozen_milestone(self)
        self.root = _build_probe_root(self)

    def test_unknown_finding_id_raises(self) -> None:
        with self.assertRaises(r1.R1Error):
            r1.verify_candidate_finding("V16-R1-999", self.root)
        with self.assertRaises(r1.R1Error):
            r1.verify_candidate_finding("", self.root)

    def test_known_finding_id_returns_true(self) -> None:
        # V16-R1-005: an absolute shell entrypoint must not compile.
        outcome = r1.verify_candidate_finding("V16-R1-005", self.root)
        self.assertIsInstance(outcome, bool)
        self.assertIs(outcome, True)

    def test_known_finding_id_fails_closed_without_frozen_milestone(self) -> None:
        # V16-R1-001 asserts the frozen dispatch transcript still validates.
        # With no frozen baseline it must report False, never an unearned True.
        outcome = r1.verify_candidate_finding("V16-R1-001", self.root)
        self.assertIsInstance(outcome, bool)
        self.assertIs(outcome, False)


if __name__ == "__main__":
    unittest.main()
