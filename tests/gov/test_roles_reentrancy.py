"""The mission contract must behave identically under any roles configuration.

Placeholder roles disable the reviewer-identity assertion by design, so a suite
that only ever runs unresolved can hide both real breakage and a silently
disabled gate.  These tests pin the roles config explicitly in both directions
and prove the assertion is still strict once identities resolve.

Reasoning effort is configuration in exactly the same way, and it has no
placeholder escape hatch at all, so an effort assertion that is silently
hard-coded stays green under the default profile and only breaks on a user's
machine.  ``ReasoningEffortReentrancyTests`` pins two different effort profiles
and proves the gate both follows the configuration and stays strict under it.
"""
from __future__ import annotations

import copy
import json
import pathlib
import unittest

from support import (
    RESOLVED_TEST_ROLES,
    effort_for_risk,
    mission_with_current_reviewer,
    pinned_roles,
)
from test_trace_verdict import decision_basis, trace_packet
from zgov import roles
from zgov.compiler import compile_mission
from zgov.contracts import ContractError, canonical_sha256
from zgov.review_policy import resolve_reviewer
from zgov.trace import TraceError

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "gov" / "zgov" / "fixtures"
VALID_MISSION = json.loads((FIXTURES / "mission.valid.json").read_text(encoding="utf-8"))

WRONG_MODEL = "zgov-not-the-independent-reviewer"

# The shipped default profile, and a deliberately different one.  ``roles``
# requires ``delta_continuation == reviewer_standard``.
DEFAULT_EFFORTS = dict(roles.DEFAULT_ROLES["efforts"])
ALT_EFFORTS = {
    "reviewer_standard": "medium",
    "reviewer_high": "high",
    "delta_continuation": "medium",
    "auditor_spark": "medium",
}


class RolesReentrancyTests(unittest.TestCase):
    def test_mission_compiles_under_placeholder_roles(self) -> None:
        pinned_roles(self, resolved=False)
        self.assertFalse(roles.roles_resolved())
        self.assertTrue(roles.is_placeholder(resolve_reviewer("high")["model"]))

        plan = compile_mission(mission_with_current_reviewer(VALID_MISSION))
        self.assertEqual(plan["mission_id"], VALID_MISSION["mission_id"])

    def test_mission_compiles_under_resolved_roles(self) -> None:
        pinned_roles(self, resolved=True)
        self.assertTrue(roles.roles_resolved())
        self.assertEqual(
            resolve_reviewer("high")["model"], RESOLVED_TEST_ROLES["reviewer_high"]
        )

        mission = mission_with_current_reviewer(VALID_MISSION)
        self.assertEqual(
            mission["reviewer_separation"]["independent_model"],
            RESOLVED_TEST_ROLES["reviewer_high"],
        )
        plan = compile_mission(mission)
        self.assertEqual(plan["mission_id"], VALID_MISSION["mission_id"])

    def test_wrong_reviewer_model_is_rejected_under_resolved_roles(self) -> None:
        """The identity gate is skipped for placeholders, not permanently off."""
        pinned_roles(self, resolved=True)
        mission = mission_with_current_reviewer(VALID_MISSION, model=WRONG_MODEL)
        self.assertNotEqual(WRONG_MODEL, resolve_reviewer("high")["model"])

        with self.assertRaises(ContractError) as ctx:
            compile_mission(mission)
        self.assertEqual(
            str(ctx.exception),
            "$.reviewer_separation.independent_model: reviewer model must be "
            + RESOLVED_TEST_ROLES["reviewer_high"],
        )

    def test_reviewer_identity_follows_the_configured_role_not_a_literal(self) -> None:
        """Renaming the configured reviewer moves the accepted identity with it."""
        renamed = "zgov-test-reviewer-high-renamed"
        pinned_roles(self, resolved=True, overrides={"reviewer_high": renamed})

        compile_mission(mission_with_current_reviewer(VALID_MISSION))

        stale = mission_with_current_reviewer(
            VALID_MISSION, model=RESOLVED_TEST_ROLES["reviewer_high"]
        )
        with self.assertRaises(ContractError) as ctx:
            compile_mission(stale)
        self.assertEqual(
            str(ctx.exception),
            f"$.reviewer_separation.independent_model: reviewer model must be {renamed}",
        )


class ReasoningEffortReentrancyTests(unittest.TestCase):
    """The trace decision-basis effort gate must track ``efforts``, not a literal."""

    def test_trace_flow_under_default_effort_profile(self) -> None:
        pinned_roles(self, resolved=True, efforts=DEFAULT_EFFORTS)
        self.assertEqual(effort_for_risk("low"), "high")
        self.assertEqual(effort_for_risk("high"), "xhigh")

        for risk in ("low", "medium", "high"):
            with self.subTest(risk=risk):
                packet = trace_packet(basis=decision_basis(1, risk))
                self.assertEqual(
                    packet["decision_basis"]["reasoning_effort"], effort_for_risk(risk)
                )

    def test_trace_flow_under_non_default_effort_profile(self) -> None:
        """The same flow passes once every effort is derived from the config."""
        pinned_roles(self, resolved=True, efforts=ALT_EFFORTS)
        self.assertEqual(effort_for_risk("low"), "medium")
        self.assertEqual(effort_for_risk("high"), "high")
        # Guard the guard: this profile must actually differ from the default,
        # otherwise the case proves nothing.
        self.assertNotEqual(effort_for_risk("high"), DEFAULT_EFFORTS["reviewer_high"])

        for risk in ("low", "medium", "high"):
            with self.subTest(risk=risk):
                packet = trace_packet(basis=decision_basis(1, risk))
                self.assertEqual(
                    packet["decision_basis"]["reasoning_effort"], effort_for_risk(risk)
                )

    def test_stale_default_effort_is_rejected_under_non_default_profile(self) -> None:
        """The effort assertion is still strict after the config moves.

        Without this case, deleting the effort comparison entirely would leave
        the two cases above green.
        """
        pinned_roles(self, resolved=True, efforts=ALT_EFFORTS)
        stale = DEFAULT_EFFORTS["reviewer_high"]
        self.assertEqual(stale, "xhigh")
        self.assertNotEqual(stale, effort_for_risk("high"))

        basis = copy.deepcopy(decision_basis(1, "high"))
        basis["reasoning_effort"] = stale
        # Re-seal the policy hash over the stale effort so the *only* remaining
        # inconsistency is effort-vs-config.  Otherwise the hash gate fires
        # first and this case would pass even with the effort check deleted.
        basis["review_policy_sha256"] = canonical_sha256(
            {
                "required_stages": basis["required_stages"],
                "review_risk": basis["review_risk"],
                "reviewer_route": basis["reviewer_route"],
                "reviewer_model": basis["reviewer_model"],
                "reasoning_effort": stale,
                "classifier_identity": basis["classifier_identity"],
                "high_risk_triggers": basis["high_risk_triggers"],
            }
        )

        with self.assertRaises(TraceError) as ctx:
            trace_packet(basis=basis)
        self.assertEqual(
            str(ctx.exception),
            "$.decision_basis: risk/route/model/effort policy mismatch",
        )


if __name__ == "__main__":
    unittest.main()
