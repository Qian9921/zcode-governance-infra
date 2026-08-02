"""BudgetLedger hard limits, source-bound metrics, and no-zero-collapse checks."""
from __future__ import annotations

import unittest

from zgov import metrics


def _limits(**overrides: int) -> dict[str, int]:
    base = {
        "max_model_calls": 10,
        "max_review_calls": 10,
        "max_parallel_agents": 10,
        "max_input_tokens": 10_000,
        "max_output_tokens": 10_000,
        "max_total_tokens": 20_000,
    }
    base.update(overrides)
    return base


class BudgetLedgerHardLimits(unittest.TestCase):
    """Reservations must fail closed before any limit could be exceeded."""

    def test_max_model_calls_exceeded(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits(max_model_calls=2))
        ledger.reserve(is_review=False)
        ledger.reserve(is_review=False)
        with self.assertRaises(metrics.RoutingError) as ctx:
            ledger.reserve(is_review=False)
        self.assertIn("routing budget exhausted", str(ctx.exception))
        self.assertEqual(ledger.usage()["model_calls"], 2)

    def test_max_review_calls_exceeded(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits(max_review_calls=1))
        ledger.reserve(is_review=True)
        with self.assertRaises(metrics.RoutingError):
            ledger.reserve(is_review=True)
        self.assertEqual(ledger.usage()["review_calls"], 1)

    def test_max_parallel_agents_exceeded(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits(max_parallel_agents=2))
        ledger.reserve(is_review=False, agents=2)
        with self.assertRaises(metrics.RoutingError):
            ledger.reserve(is_review=False, agents=1)
        self.assertEqual(ledger.usage()["peak_parallel_agents"], 2)

    def test_max_total_tokens_exceeded(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits(max_total_tokens=100))
        ledger.reserve(is_review=False, input_tokens=60, output_tokens=30)
        with self.assertRaises(metrics.RoutingError):
            ledger.reserve(is_review=False, input_tokens=20, output_tokens=0)

    def test_max_input_tokens_exceeded(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits(max_input_tokens=50))
        with self.assertRaises(metrics.RoutingError):
            ledger.reserve(is_review=False, input_tokens=51)

    def test_settle_actual_above_reserved_rejected(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits())
        rid = ledger.reserve(is_review=False, input_tokens=100, output_tokens=100)
        with self.assertRaises(metrics.RoutingError) as ctx:
            ledger.settle(rid, input_tokens=101, output_tokens=100)
        self.assertIn("actual token use exceeds the reserved maximum", str(ctx.exception))

    def test_unavailable_counts_charge_reserved_maxima(self) -> None:
        """Conservative fail-closed billing: usage is the upper bound, not zero."""
        ledger = metrics.BudgetLedger(limits=_limits())
        rid = ledger.reserve(is_review=False, input_tokens=500, output_tokens=250)
        ledger.settle(rid, input_tokens=500, output_tokens=250, counts_available=False)
        usage = ledger.usage()
        self.assertEqual(usage["charged_input_tokens"], 500)
        self.assertEqual(usage["charged_output_tokens"], 250)
        self.assertEqual(usage["charged_total_tokens"], 750)
        self.assertNotEqual(usage["charged_total_tokens"], 0)
        self.assertEqual(usage["token_count_basis"], "upper-bound-mixed")
        self.assertEqual(usage["maximum_charged_calls"], 1)

    def test_unavailable_counts_below_maxima_rejected(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits())
        rid = ledger.reserve(is_review=False, input_tokens=500, output_tokens=250)
        with self.assertRaises(metrics.RoutingError) as ctx:
            ledger.settle(rid, input_tokens=0, output_tokens=0, counts_available=False)
        self.assertIn("unavailable counts must charge the reserved maxima", str(ctx.exception))

    def test_unknown_reservation_rejected(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits())
        with self.assertRaises(metrics.RoutingError):
            ledger.settle("call-999", input_tokens=0, output_tokens=0)

    def test_exact_budget_fields_required(self) -> None:
        with self.assertRaises(metrics.RoutingError):
            metrics.BudgetLedger(limits={"max_model_calls": 1})


class MetricsSourceBound(unittest.TestCase):
    """Metrics are only accepted when recomputed from their source bundle."""

    def test_missing_source_bundle_rejected(self) -> None:
        packet = {
            "schema": "metrics.v16",
            "mission_id": "M-1",
            "source_hash": "a" * 64,
            "first_pass_approval": True,
            "pre_review_blocker_capture": 0,
            "review_rounds": 1,
            "full_runs_per_head": None,
            "fresh_runs_per_head": None,
            "evidence_corrections": 0,
            "writer_handoffs": 0,
            "spark_audit_count": 0,
            "spark_audit_latency_sec": 0.0,
            "gate_elapsed_sec": 0.0,
            "new_blocker_admissions": 0,
        }
        with self.assertRaises(metrics.MetricsError) as ctx:
            metrics.validate_metrics(packet)
        self.assertIn("source bundle required", str(ctx.exception))

    def test_malformed_source_bundle_rejected(self) -> None:
        packet = {
            "schema": "metrics.v16",
            "mission_id": "M-1",
            "source_hash": "a" * 64,
            "first_pass_approval": True,
            "pre_review_blocker_capture": 0,
            "review_rounds": 1,
            "full_runs_per_head": None,
            "fresh_runs_per_head": None,
            "evidence_corrections": 0,
            "writer_handoffs": 0,
            "spark_audit_count": 0,
            "spark_audit_latency_sec": 0.0,
            "gate_elapsed_sec": 0.0,
            "new_blocker_admissions": 0,
        }
        with self.assertRaises(metrics.MetricsError) as ctx:
            metrics.validate_metrics(packet, source_bundle={"mission": {}})
        self.assertIn("validated metrics source bundle required", str(ctx.exception))

    def test_bad_source_hash_shape_rejected(self) -> None:
        packet = {
            "schema": "metrics.v16",
            "mission_id": "M-1",
            "source_hash": "not-a-sha",
            "first_pass_approval": True,
            "pre_review_blocker_capture": 0,
            "review_rounds": 1,
            "full_runs_per_head": None,
            "fresh_runs_per_head": None,
            "evidence_corrections": 0,
            "writer_handoffs": 0,
            "spark_audit_count": 0,
            "spark_audit_latency_sec": 0.0,
            "gate_elapsed_sec": 0.0,
            "new_blocker_admissions": 0,
        }
        with self.assertRaises(metrics.MetricsError) as ctx:
            metrics.validate_metrics_shape(packet)
        self.assertIn("source_hash must be SHA-256", str(ctx.exception))

    def test_review_efficiency_requires_source_bundle(self) -> None:
        with self.assertRaises(metrics.MetricsError) as ctx:
            metrics.validate_review_efficiency_metrics({"schema": metrics.REVIEW_EFFICIENCY_SCHEMA})
        self.assertIn("source bundle required", str(ctx.exception))


class MissingDataStaysUnavailable(unittest.TestCase):
    """Unavailable values must remain None/unavailable, never collapse to 0."""

    def test_runtime_metrics_keep_none(self) -> None:
        observed = {"ttft_sec": None, "decode_sec": None, "tool_sec": None, "quality": None}
        result = metrics.collect_runtime_metrics(observed)
        for name in metrics.RUNTIME_METRIC_FIELDS:
            self.assertIsNone(result[name], f"{name} collapsed to a value")
            self.assertNotEqual(result[name], 0)

    def test_runtime_metrics_mixed_availability(self) -> None:
        observed = {"ttft_sec": 1.5, "decode_sec": None, "tool_sec": 0.0, "quality": None}
        result = metrics.collect_runtime_metrics(observed)
        self.assertEqual(result["ttft_sec"], 1.5)
        self.assertIsNone(result["decode_sec"])
        self.assertEqual(result["tool_sec"], 0.0)
        self.assertIsNone(result["quality"])

    def test_ledger_cost_is_unavailable_not_zero(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits())
        usage = ledger.usage()
        self.assertIsNone(usage["usd_cost"])
        self.assertNotEqual(usage["usd_cost"], 0)
        self.assertEqual(usage["usd_cost_reason"], "unavailable-without-provider-plan-attribution")

    def test_fresh_ledger_basis_is_provider_reported(self) -> None:
        ledger = metrics.BudgetLedger(limits=_limits())
        self.assertEqual(ledger.usage()["token_count_basis"], "provider-reported")


class ModelRoutingNoImplicitFallback(unittest.TestCase):
    """choose_model refuses rather than silently substituting a model."""

    def test_no_authorized_live_model_raises(self) -> None:
        with self.assertRaises(metrics.RoutingError) as ctx:
            metrics.choose_model(
                task_kind="review",
                risk="high",
                authorized_models=["model-a"],
                live_models={"model-a": {"available": False}},
                preferences=None,
            )
        self.assertIn("no authorized live model", str(ctx.exception))

    def test_risk_mismatch_is_not_silently_downgraded(self) -> None:
        with self.assertRaises(metrics.RoutingError):
            metrics.choose_model(
                task_kind="review",
                risk="high",
                authorized_models=["model-a"],
                live_models={"model-a": {"available": True, "risks": ["low"]}},
                preferences=None,
            )

    def test_selects_authorized_live_model(self) -> None:
        chosen = metrics.choose_model(
            task_kind="review",
            risk="high",
            authorized_models=["model-a", "model-b"],
            live_models={
                "model-a": {"available": True, "risks": ["high"], "token_cost_rank": 2},
                "model-b": {"available": True, "risks": ["high"], "token_cost_rank": 1},
            },
            preferences=None,
        )
        self.assertEqual(chosen, "model-b")


if __name__ == "__main__":
    unittest.main()
