"""Intent-routing, ZCode tool-name normalisation, and fallback-legitimacy tests."""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
import unittest

from zgov import tool_routing as tr


EXPECTED_ROUTES = {
    "known_symbol": "codegraph",
    "known_call": "codegraph",
    "blast_radius": "codegraph",
    "semantic_entry": "semble",
    "similar_implementation": "semble",
    "shell_output": "rtk",
    "exact_string": "rg",
    "exact_error": "rg",
    "config": "rg",
    "log": "rg",
}


def _usable(*tools: str) -> list[tr.ToolObservation]:
    return [
        tr.ToolObservation(tool=tool, available=True, healthy=True, evidence_ref=f"probe:{tool}")
        for tool in tools
    ]


class IntentRoutingTest(unittest.TestCase):
    """The authoritative intent -> tool mapping is exhaustive and stable."""

    def test_intent_enum_has_ten_members(self) -> None:
        self.assertEqual(len(list(tr.Intent)), 10)
        self.assertEqual({i.value for i in tr.Intent}, set(EXPECTED_ROUTES))

    def test_preferred_tool_mapping(self) -> None:
        for intent, tool in EXPECTED_ROUTES.items():
            with self.subTest(intent=intent):
                self.assertEqual(tr.PREFERRED_TOOL[intent], tool)

    def test_route_tool_selects_preferred(self) -> None:
        for intent, tool in EXPECTED_ROUTES.items():
            with self.subTest(intent=intent):
                decision = tr.route_tool(intent, observations=_usable(tool))
                self.assertEqual(decision["preferred_tool"], tool)
                self.assertEqual(decision["selected_tool"], tool)
                self.assertEqual(decision["decision"], "route")
                self.assertFalse(decision["fallback"])

    def test_fallback_chain(self) -> None:
        self.assertEqual(
            tr.FALLBACK_TOOL,
            {"codegraph": "rg", "semble": "rg", "rtk": "shell", "rg": "shell"},
        )


class NormalizeZcodeToolTest(unittest.TestCase):
    """normalize_zcode_tool is purely lexical: no allow/deny, no intent inference."""

    CASES = (
        ("mcp__codegraph__codegraph_explore", None, "codegraph"),
        ("mcp__semble__search", None, "semble"),
        ("mcp__semble__find_related", None, "semble"),
        ("mcp__codegraph__anything_else", None, "codegraph"),
        ("Grep", None, "rg"),
        ("Bash", {"command": "rtk git status"}, "rtk"),
        ("Bash", {"command": "rg -n foo src/"}, "rg"),
        ("Bash", {"command": "/usr/bin/rg -n foo"}, "rg"),
        ("Bash", {"command": "printf x | rtk rg p"}, None),
        ("Bash", {"command": "git status"}, None),
        ("Bash", None, None),
        ("Read", None, None),
    )

    def test_cases(self) -> None:
        for tool_name, tool_input, expected in self.CASES:
            with self.subTest(tool=tool_name, input=tool_input):
                self.assertEqual(tr.normalize_zcode_tool(tool_name, tool_input), expected)

    def test_denominator(self) -> None:
        self.assertEqual(len(self.CASES), 12)

    def test_every_alias_resolves_to_a_known_tool(self) -> None:
        for alias, tool in tr.ZCODE_TOOL_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertIn(tool, tr.TOOLS)
                self.assertEqual(tr.normalize_zcode_tool(alias), tool)


class FallbackLegitimacyTest(unittest.TestCase):
    """A fallback is only legitimate with an attempt, a reason, and evidence."""

    def test_fallback_without_preferred_attempt_is_blocked(self) -> None:
        decision = tr.route_tool(
            "known_symbol",
            observations=[tr.ToolObservation(tool="codegraph", available=False)] + _usable("rg"),
        )
        self.assertEqual(decision["decision"], "blocked")
        self.assertEqual(decision["reason_code"], "PREFERRED_NOT_ATTEMPTED")
        self.assertIsNone(decision["selected_tool"])

    def test_forged_fallback_decision_is_rejected(self) -> None:
        forged = {
            "schema": tr.ROUTE_SCHEMA,
            "intent": "known_symbol",
            "declared": True,
            "preferred_tool": "codegraph",
            "selected_tool": "rg",
            "decision": "fallback",
            "status": "fallback",
            "fallback": True,
            "attempted_preferred": False,
            "reason_code": "CODEGRAPH_INDEX_MISSING",
            "evidence_ref": "probe:codegraph",
        }
        with self.assertRaises(tr.RoutingError):
            tr.validate_route_decision(forged)

    def test_reason_code_without_evidence_ref_is_blocked(self) -> None:
        decision = tr.route_tool(
            "known_symbol",
            observations=_usable("rg"),
            attempted_preferred=True,
            failure_reason_code="CODEGRAPH_INDEX_MISSING",
            evidence_ref=None,
        )
        self.assertEqual(decision["decision"], "blocked")
        self.assertEqual(decision["reason_code"], "FALLBACK_EVIDENCE_REQUIRED")
        self.assertIsNone(decision["selected_tool"])

    def test_legitimate_fallback_is_allowed(self) -> None:
        decision = tr.route_tool(
            "known_symbol",
            observations=_usable("rg"),
            attempted_preferred=True,
            failure_reason_code="CODEGRAPH_INDEX_MISSING",
            evidence_ref="probe:codegraph",
        )
        self.assertEqual(decision["decision"], "fallback")
        self.assertEqual(decision["selected_tool"], "rg")
        self.assertTrue(decision["fallback"])
        self.assertEqual(tr.validate_route_decision(decision), decision)


SHA_ZERO = "0" * 64
HEAD_SHA = "1" * 40


def _preflight_artifact() -> dict:
    def tool(name: str) -> dict:
        return {
            "tool": name,
            "status": "pass",
            "reason_code": "OK",
            "version": "1.0.0",
            "checks": [{"name": "present", "status": "pass", "reason_code": "OK"}],
            "evidence_sha256": SHA_ZERO,
        }

    return {
        "schema": "tool-preflight.v16",
        "status": "ready",
        "strict": True,
        "repo_identity": {
            "root_sha256": SHA_ZERO,
            "head_sha": HEAD_SHA,
            "dirty": False,
            "worktree_sha256": SHA_ZERO,
        },
        "config_identity": {
            "path_sha256": SHA_ZERO,
            "content_sha256": SHA_ZERO,
            "present": True,
        },
        "tools": [tool("codegraph"), tool("semble"), tool("rtk")],
        "counts": {
            "total": 3, "ran": 3, "passed": 3,
            "failed": 0, "skipped": 0, "xfail": 0, "unknown": 0,
        },
        "denominator": 3,
        "denominator_known": True,
        "cache": {"key_sha256": SHA_ZERO, "invalidated_by": ["head_sha"]},
        "mutations": [],
    }


def _receipt(*, tool_name: str, route: str, call_id: str, snapshot: str, task: str) -> dict:
    return {
        "schema": "hook-receipt.v16",
        "schema_version": "hook-receipt.v16",
        "utc": "2026-01-01T00:00:00Z",
        "event": "PreToolUse",
        "model": "test-model",
        "tool_name": tool_name,
        "decision": "allow",
        "reason": "routed",
        "reason_code": "ROUTED",
        "route": route,
        "route_code": route,
        "snapshot_sha256": snapshot,
        "identifiers_sha256": task,
        "session_id_sha256": None,
        "turn_id_sha256": None,
        "tool_call_id_sha256": call_id,
        "source": "test",
        "pid": 1,
        "ppid": 1,
        "receipt_status": "written",
    }


def _sha_file(path: pathlib.Path, payload: bytes) -> str:
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


class UsageCoverageEquivalenceTest(unittest.TestCase):
    """A fallback may be routing compliant but never coverage equivalent."""

    def _build(self, tmp: pathlib.Path, *, fallback: bool):
        snapshot = "a" * 64
        task = "b" * 64
        call_id = "c" * 64

        preflight_path = tmp / "preflight.json"
        preflight_sha = _sha_file(
            preflight_path,
            json.dumps(_preflight_artifact()).encode("utf-8"),
        )

        if fallback:
            route = tr.route_tool(
                "known_symbol",
                observations=_usable("rg"),
                attempted_preferred=True,
                failure_reason_code="CODEGRAPH_INDEX_MISSING",
                evidence_ref="probe:codegraph",
            )
            tool_name, route_code = "rg", "rg"
        else:
            route = tr.route_tool("known_symbol", observations=_usable("codegraph"))
            tool_name, route_code = "mcp__codegraph__codegraph_explore", "codegraph"

        receipt = _receipt(
            tool_name=tool_name, route=route_code, call_id=call_id,
            snapshot=snapshot, task=task,
        )
        receipt_line = (
            json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        )
        receipt_sha = hashlib.sha256(receipt_line.encode("utf-8")).hexdigest()
        receipt_path = tmp / "receipts.jsonl"
        receipt_file_sha = _sha_file(receipt_path, receipt_line.encode("utf-8"))

        evidence_path = tmp / "evidence.txt"
        evidence_sha = _sha_file(evidence_path, b"evidence payload\n")

        call = {
            "intent": "known_symbol",
            "tool": route["selected_tool"],
            "status": "success",
            "evidence_ref": "ev:1",
            "evidence_sha256": evidence_sha,
            "receipt_sha256": receipt_sha,
            "tool_call_id_sha256": call_id,
            "used_for": tr.USAGE_PURPOSE["known_symbol"],
        }
        kwargs = dict(
            preflight_artifact=preflight_path,
            expected_preflight_artifact_sha256=preflight_sha,
            receipt_artifacts=[receipt_path],
            expected_receipt_artifact_sha256s=[receipt_file_sha],
            evidence_artifacts={"ev:1": evidence_path},
            expected_evidence_sha256={"ev:1": evidence_sha},
        )
        report = tr.build_usage_report(
            hook_snapshot_sha256=snapshot,
            task_id_sha256=task,
            routes=[route],
            calls=[call],
            **kwargs,
        )
        return report, kwargs

    def test_preferred_route_is_coverage_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report, _ = self._build(pathlib.Path(raw), fallback=False)
            self.assertTrue(report["routing_compliant"])
            self.assertTrue(report["coverage_equivalent"])
            self.assertEqual(report["status"], "compliant")

    def test_fallback_is_compliant_but_not_coverage_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            report, _ = self._build(pathlib.Path(raw), fallback=True)
            self.assertTrue(report["routing_compliant"])
            self.assertFalse(report["coverage_equivalent"])
            self.assertEqual(report["status"], "degraded")

    def test_fallback_claiming_coverage_equivalent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            report, kwargs = self._build(tmp, fallback=True)
            forged = dict(report)
            forged["coverage_equivalent"] = True
            forged["status"] = "compliant"
            with self.assertRaises(tr.RoutingError):
                tr.validate_usage_report(forged, **kwargs)


if __name__ == "__main__":
    unittest.main()
