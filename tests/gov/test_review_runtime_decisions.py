"""Decision-priority and budget-profile tests for zgov.review_runtime."""

import unittest

from zgov import review_runtime as rr


SHA = "a" * 64


def _policy(risk="low"):
    policy = {"review_policy": {"review_risk": risk}}
    if risk in ("low", "medium"):
        policy["review_policy"]["reasons"] = ["test"]
        policy["review_policy"]["classifier_identity"] = "human"
    else:
        policy["review_policy"]["high_risk_triggers"] = ["security"]
    return policy


def _make_runtime(context_mode="independent_clean_room", risk="low"):
    policy = _policy(risk)

    prior = SHA if context_mode in ("delta_continuation", "escalated_fresh") else ""
    continuity = SHA if context_mode == "delta_continuation" else ""
    prior_status = "COMPLETE" if context_mode == "delta_continuation" else ""
    triggers = ["security"] if context_mode == "escalated_fresh" else ()

    return rr.compile_review_runtime(
        policy,
        context_mode=context_mode,
        changed_files=1,
        changed_lines=1,
        review_identity_sha256=SHA,
        prior_review_artifact_sha256=prior,
        reviewer_continuity_id=continuity,
        prior_coverage_status=prior_status,
        prior_unreviewed_count=0,
        contract_drift=False,
        same_reviewer_available=(context_mode == "delta_continuation"),
        escalation_triggers=triggers,
    )


def _expectations(context_mode="independent_clean_room"):
    prior = SHA if context_mode in ("delta_continuation", "escalated_fresh") else ""
    continuity = SHA if context_mode == "delta_continuation" else ""
    return {
        "context_mode": context_mode,
        "changed_files": 1,
        "changed_lines": 1,
        "review_identity_sha256": SHA,
        "prior_review_artifact_sha256": prior,
        "reviewer_continuity_id": continuity,
    }


class TestBudgetProfiles(unittest.TestCase):
    def test_low_medium_profile(self):
        for risk in ("low", "medium"):
            runtime = _make_runtime(risk=risk)
            self.assertEqual(runtime["soft_deadline_sec"], 180)
            self.assertEqual(runtime["hard_deadline_sec"], 480)
            self.assertEqual(runtime["max_files"], 20)
            self.assertEqual(runtime["max_changed_lines"], 3000)
            self.assertEqual(runtime["max_context_chars"], 20000)
            self.assertEqual(runtime["max_tool_calls"], 12)
            self.assertGreater(runtime["hard_deadline_sec"], runtime["soft_deadline_sec"])

    def test_high_profile(self):
        runtime = _make_runtime(risk="high")
        self.assertEqual(runtime["soft_deadline_sec"], 300)
        self.assertEqual(runtime["hard_deadline_sec"], 900)
        self.assertEqual(runtime["max_files"], 24)
        self.assertEqual(runtime["max_changed_lines"], 5000)
        self.assertEqual(runtime["max_context_chars"], 24000)
        self.assertEqual(runtime["max_tool_calls"], 16)
        self.assertGreater(runtime["hard_deadline_sec"], runtime["soft_deadline_sec"])

    def test_delta_profile(self):
        runtime = _make_runtime(context_mode="delta_continuation", risk="low")
        self.assertEqual(runtime["soft_deadline_sec"], 90)
        self.assertEqual(runtime["hard_deadline_sec"], 240)
        self.assertEqual(runtime["max_files"], 12)
        self.assertEqual(runtime["max_changed_lines"], 800)
        self.assertEqual(runtime["max_context_chars"], 12000)
        self.assertEqual(runtime["max_tool_calls"], 8)
        self.assertGreater(runtime["hard_deadline_sec"], runtime["soft_deadline_sec"])


class TestDecisionPriority(unittest.TestCase):
    def _decide(self, runtime, risk="low", **kwargs):
        defaults = {
            "elapsed_sec": 0,
            "tool_calls": 0,
            "files_read": 0,
            "context_chars": 0,
            "review_calls": 0,
            "duplicate_full_scope_reviews": 0,
            "verdict_present": False,
            "coverage_complete": False,
            "unreviewed_count": 0,
            "scope_expansion_requested": False,
            "new_falsifiable_evidence": False,
        }
        defaults.update(kwargs)
        return rr.review_progress_decision(
            runtime,
            policy_or_mission=_policy(risk),
            runtime_expectations=_expectations(runtime["context_mode"]),
            **defaults,
        )

    def test_interrupt_replan_on_hard_budget(self):
        runtime = _make_runtime()
        result = self._decide(runtime, elapsed_sec=runtime["hard_deadline_sec"])
        self.assertEqual(result["action"], "INTERRUPT_REPLAN")
        self.assertEqual(result["reason_code"], "HARD_RUNTIME_BUDGET_EXCEEDED")
        self.assertFalse(result["approval_eligible"])

    def test_escalate_fresh_on_delta_new_evidence(self):
        runtime = _make_runtime(context_mode="delta_continuation")
        result = self._decide(runtime, new_falsifiable_evidence=True)
        self.assertEqual(result["action"], "ESCALATE_FRESH")
        self.assertEqual(result["reason_code"], "NEW_FALSIFIABLE_EVIDENCE")
        self.assertFalse(result["approval_eligible"])

    def test_stop_scope_expansion(self):
        runtime = _make_runtime()
        result = self._decide(runtime, scope_expansion_requested=True)
        self.assertEqual(result["action"], "STOP_SCOPE_EXPANSION")
        self.assertEqual(result["reason_code"], "SCOPE_EXPANSION_LACKS_COUNTEREXAMPLE")
        self.assertFalse(result["approval_eligible"])

    def test_accept_report(self):
        runtime = _make_runtime()
        result = self._decide(
            runtime,
            verdict_present=True,
            review_calls=runtime["max_review_calls"],
            coverage_complete=True,
            unreviewed_count=0,
        )
        self.assertEqual(result["action"], "ACCEPT_REPORT")
        self.assertEqual(result["reason_code"], "COMPLETE_REPORT_AVAILABLE")
        self.assertTrue(result["approval_eligible"])

    def test_return_partial(self):
        runtime = _make_runtime()
        result = self._decide(
            runtime,
            verdict_present=True,
            review_calls=runtime["max_review_calls"],
            coverage_complete=False,
            unreviewed_count=1,
        )
        self.assertEqual(result["action"], "RETURN_PARTIAL")
        self.assertEqual(result["reason_code"], "VERDICT_WITH_INCOMPLETE_COVERAGE")
        self.assertFalse(result["approval_eligible"])

    def test_request_report_at_soft_deadline(self):
        runtime = _make_runtime()
        result = self._decide(runtime, elapsed_sec=runtime["soft_deadline_sec"])
        self.assertEqual(result["action"], "REQUEST_REPORT")
        self.assertEqual(result["reason_code"], "SOFT_DEADLINE_REACHED")
        self.assertFalse(result["approval_eligible"])

    def test_return_partial_is_never_approval_eligible(self):
        for unreviewed, complete in ((1, False), (1, True), (0, False)):
            with self.subTest(unreviewed=unreviewed, complete=complete):
                runtime = _make_runtime()
                result = self._decide(
                    runtime,
                    verdict_present=True,
                    review_calls=runtime["max_review_calls"],
                    coverage_complete=complete,
                    unreviewed_count=unreviewed,
                )
                self.assertEqual(result["action"], "RETURN_PARTIAL")
                self.assertFalse(result["approval_eligible"])

    def test_continue_within_budget(self):
        runtime = _make_runtime()
        result = self._decide(
            runtime,
            elapsed_sec=runtime["soft_deadline_sec"] - 1,
            tool_calls=runtime["max_tool_calls"] - 1,
            files_read=runtime["max_files"] - 1,
            context_chars=runtime["max_context_chars"] - 1,
        )
        self.assertEqual(result["action"], "CONTINUE")
        self.assertEqual(result["reason_code"], "WITHIN_RUNTIME_BUDGET")
        self.assertFalse(result["approval_eligible"])


if __name__ == "__main__":
    unittest.main()
