#!/usr/bin/env python3
"""Contract tests for gov/hooks (ZCode hook platform).

Every subprocess run is isolated: ``HOME`` points at a throwaway directory and
receipts are redirected with ``ZGOV_HOOK_SOURCE=test`` +
``ZGOV_HOOK_RECEIPT_DIR``, so no file under the real ``~/.zcode/`` is read for
state or written.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK_DIR = REPO / "gov" / "hooks"
ENTRY = HOOK_DIR / "zcode_hook.py"
CODEX_CONTRACTS = Path("/tmp/codex-gov-infra/codex/contracts")

sys.path.insert(0, str(HOOK_DIR))
import hook_receipt  # noqa: E402
import pre_tool_use_policy  # noqa: E402
import session_context  # noqa: E402
from delegation_contract import (  # noqa: E402
    FORBIDDEN_CHILD_ROLES,
    ContractError,
    validate_packet,
    validate_result,
)

ALLOWED_KEYS = frozenset(
    {
        "hookEventName",
        "permissionDecision",
        "permissionDecisionReason",
        "additionalContext",
        "updatedInput",
    }
)
SUBCOMMANDS = ("session-start", "pre-bash", "pre-file", "pre-tool", "post-agent")
GH_STUB_APPROVED = """#!/bin/sh
case "$1 $2" in
  "api user") echo "Liang9921" ;;
  "pr view") echo '{"number":123,"headRefOid":"%s","url":"https://github.com/o/r/pull/123"}' ;;
  *) exit 1 ;;
esac
""" % ("a" * 40)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class HookRun:
    """Result of one isolated hook subprocess run."""

    def __init__(self, code: int, stdout: str, stderr: str, receipts: list[dict]):
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        self.receipts = receipts

    @property
    def json(self) -> dict:
        return json.loads(self.stdout) if self.stdout.strip() else {}


class HookTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.receipt_dir = self.root / "receipts"
        self.home.mkdir()

    def run_hook(self, sub, payload, *, env=None, entry=None, args=()):
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.home),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "ZGOV_HOOK_SOURCE": "test",
            "ZGOV_HOOK_RECEIPT_DIR": str(self.receipt_dir),
            # Default to an unreachable gh so no test performs network I/O.
            "GH_PATH": str(self.root / "no-such-gh"),
        }
        environment.update(env or {})
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run(
            [sys.executable, str(entry or ENTRY), sub, *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
        )
        return HookRun(proc.returncode, proc.stdout, proc.stderr, self.read_receipts())

    def read_receipts(self):
        records = []
        if self.receipt_dir.exists():
            for path in sorted(self.receipt_dir.glob("*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        records.append(json.loads(line))
        return records

    def assertSchemaCompliant(self, run: HookRun):
        self.assertEqual(run.code, 0, f"exit={run.code} stderr={run.stderr}")
        if not run.stdout.strip():
            return {}
        value = json.loads(run.stdout)
        self.assertIsInstance(value, dict)
        extra = set(value) - ALLOWED_KEYS
        self.assertFalse(extra, f"stdout carries non-allowlisted key(s): {sorted(extra)}")
        return value

    def gh_stub(self, body: str) -> str:
        path = self.root / "gh-stub"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o700)
        return str(path)


# ---------------------------------------------------------------- 1 + 2
class StdoutContractTests(HookTestCase):
    PAYLOADS = {
        "session-start": {"cwd": "/tmp", "session_id": "s-1"},
        "pre-bash": {"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
        "pre-file": {"tool_name": "Read", "tool_input": {"file_path": "/tmp/plain.txt"}},
        "pre-tool": {"tool_name": "Agent", "tool_input": {"subagent_type": "x"}},
        "post-agent": {"tool_name": "Agent", "tool_input": {"subagent_type": "x"}, "success": True},
    }

    def test_every_subcommand_emits_only_allowlisted_keys(self):
        for sub in SUBCOMMANDS:
            with self.subTest(sub=sub):
                self.assertSchemaCompliant(self.run_hook(sub, self.PAYLOADS[sub]))

    def test_deny_paths_also_exit_zero_and_stay_in_schema(self):
        cases = {
            "pre-bash": {"tool_name": "Bash", "tool_input": {"command": "cat ~/.ssh/id_rsa"}},
            "pre-file": {"tool_name": "Read", "tool_input": {"file_path": "~/.ssh/id_rsa"}},
            "pre-tool": {"tool_name": "Agent", "tool_input": {"subagent_type": "x"}},
        }
        env = {"pre-tool": {"ZGOV_AGENT_DEPTH": "1"}}
        for sub, payload in cases.items():
            with self.subTest(sub=sub):
                run = self.run_hook(sub, payload, env=env.get(sub))
                value = self.assertSchemaCompliant(run)
                self.assertEqual(value.get("permissionDecision"), "deny")
                self.assertEqual(run.code, 0)

    def test_session_start_context_is_bounded(self):
        run = self.run_hook("session-start", {"cwd": "/tmp"})
        value = self.assertSchemaCompliant(run)
        self.assertLessEqual(len(value["additionalContext"]), 1200)
        for token in ("CodeGraph", "Semble", "rtk", "rg", "tool-preflight.v16"):
            self.assertIn(token, value["additionalContext"])


# ---------------------------------------------------------------- 3
class ExistingGuardRegressionTests(HookTestCase):
    def bash(self, command, **kwargs):
        return self.run_hook("pre-bash", {"tool_name": "Bash", "tool_input": {"command": command}}, **kwargs)

    def test_credential_guard_denies_ssh_key(self):
        value = self.assertSchemaCompliant(self.bash("cat ~/.ssh/id_rsa"))
        self.assertEqual(value["permissionDecision"], "deny")
        self.assertIn("/.ssh/", value["permissionDecisionReason"])

    def test_credential_allowlist_permits_register_hooks(self):
        run = self.bash("python3 scripts/register-hooks.py --config ~/.zcode/cli/config.json")
        value = self.assertSchemaCompliant(run)
        self.assertNotIn("permissionDecision", value)
        self.assertEqual(
            [r["reason_code"] for r in run.receipts],
            ["credential.allowlisted_register_hooks"],
        )

    def test_credential_allowlist_is_not_over_broad(self):
        value = self.assertSchemaCompliant(self.bash("cat ~/.zcode/cli/config.json"))
        self.assertEqual(value["permissionDecision"], "deny")

    def test_credential_allowlist_does_not_cover_other_paths(self):
        value = self.assertSchemaCompliant(
            self.bash("scripts/register-hooks.py ~/.zcode/cli/config.json /etc/shadow")
        )
        self.assertEqual(value["permissionDecision"], "deny")

    def test_rtk_rewrite_prefixes_supported_command(self):
        value = self.assertSchemaCompliant(self.bash("git status"))
        self.assertEqual(value["updatedInput"]["command"], "rtk git status")

    def test_rtk_rewrite_skips_compound_command(self):
        value = self.assertSchemaCompliant(self.bash("git status | head"))
        self.assertNotIn("updatedInput", value)

    def test_merge_gate_denies_without_marker(self):
        env = {"GH_PATH": self.gh_stub(GH_STUB_APPROVED)}
        run = self.bash("gh pr merge 123", env=env)
        value = self.assertSchemaCompliant(run)
        self.assertEqual(value["permissionDecision"], "deny")
        self.assertIn("APPROVE marker", value["permissionDecisionReason"])
        self.assertEqual([r["reason_code"] for r in run.receipts], ["merge_gate.no_marker"])

    def test_merge_gate_denies_wrong_identity(self):
        stub = self.gh_stub('#!/bin/sh\ncase "$1 $2" in\n  "api user") echo Qian9921 ;;\n  *) exit 1 ;;\nesac\n')
        run = self.bash("gh pr merge 123", env={"GH_PATH": stub})
        value = self.assertSchemaCompliant(run)
        self.assertEqual(value["permissionDecision"], "deny")
        self.assertEqual([r["reason_code"] for r in run.receipts], ["merge_gate.identity"])

    def test_merge_gate_is_fail_closed_when_gh_is_unreachable(self):
        run = self.bash("gh pr merge 123")
        value = self.assertSchemaCompliant(run)
        self.assertEqual(value["permissionDecision"], "deny")
        self.assertEqual([r["reason_code"] for r in run.receipts], ["merge_gate.identity"])

    def test_identity_guard_is_fail_open_when_gh_is_unreachable(self):
        run = self.bash("git push origin main")
        value = self.assertSchemaCompliant(run)
        self.assertNotIn("permissionDecision", value)
        # fail-open: warn receipt, then the unrelated rtk rewrite still applies.
        self.assertEqual(
            [r["reason_code"] for r in run.receipts],
            ["identity.gh_unreachable", "rtk.auto_rewrite"],
        )
        self.assertEqual(value["updatedInput"]["command"], "rtk git push origin main")

    def test_identity_guard_denies_dev_action_under_governance_login(self):
        stub = self.gh_stub('#!/bin/sh\ncase "$1 $2" in\n  "api user") echo Liang9921 ;;\n  *) exit 1 ;;\nesac\n')
        run = self.bash("gh pr create --title x", env={"GH_PATH": stub})
        value = self.assertSchemaCompliant(run)
        self.assertEqual(value["permissionDecision"], "deny")
        self.assertEqual([r["reason_code"] for r in run.receipts], ["identity.mismatch"])


# ---------------------------------------------------------------- 4
class DelegationGuardTests(HookTestCase):
    PAYLOAD = {"tool_name": "Agent", "tool_input": {"subagent_type": "x"}}

    def test_depth_zero_allows_dispatch(self):
        run = self.run_hook("pre-tool", self.PAYLOAD, env={"ZGOV_AGENT_DEPTH": "0"})
        value = self.assertSchemaCompliant(run)
        self.assertNotIn("permissionDecision", value)
        self.assertEqual([r["decision"] for r in run.receipts], ["allow"])

    def test_depth_one_denies_nested_dispatch(self):
        run = self.run_hook("pre-tool", self.PAYLOAD, env={"ZGOV_AGENT_DEPTH": "1"})
        value = self.assertSchemaCompliant(run)
        self.assertEqual(value["permissionDecision"], "deny")
        self.assertIn("max_depth=1", value["permissionDecisionReason"])
        self.assertEqual([r["reason_code"] for r in run.receipts], ["delegation.depth_exceeded"])

    def test_forbidden_child_roles_starts_empty(self):
        self.assertEqual(FORBIDDEN_CHILD_ROLES, frozenset())

    def test_post_agent_never_blocks(self):
        run = self.run_hook("post-agent", {"tool_name": "Agent", "tool_input": {"subagent_type": "x"}, "success": False})
        self.assertEqual(run.stdout.strip(), "")
        self.assertEqual(run.code, 0)
        self.assertEqual(len(run.receipts), 1)
        self.assertEqual(run.receipts[0]["reason_code"], "post_agent.fail." + sha("x")[:16])
        self.assertEqual(run.receipts[0]["decision"], "allow")


# ---------------------------------------------------------------- 5
class ReceiptPrivacyTests(HookTestCase):
    def test_hook_payload_secrets_never_reach_disk(self):
        payload = {
            "tool_name": "mcp__semble__search",
            "tool_input": {"query": "SECRET-QUERY-VALUE"},
            "args": ["SECRET-ARG-VALUE"],
            "cwd": "/secret/working/dir",
            "prompt": "SECRET-PROMPT-VALUE",
            "session_id": "plain-session-1",
            "turn_id": "plain-turn-1",
            "tool_call_id": "plain-call-1",
        }
        run = self.run_hook("pre-tool", payload)
        self.assertSchemaCompliant(run)
        self.assertEqual(len(run.receipts), 1)
        record = run.receipts[0]
        raw = json.dumps(record)
        for key in ("args", "cwd", "prompt", "session_id", "turn_id", "tool_call_id", "tool_input"):
            self.assertNotIn(key, record)
        for secret in (
            "SECRET-QUERY-VALUE", "SECRET-ARG-VALUE", "/secret/working/dir",
            "SECRET-PROMPT-VALUE", "plain-session-1", "plain-turn-1", "plain-call-1",
        ):
            self.assertNotIn(secret, raw)
        self.assertEqual(record["session_id_sha256"], sha("plain-session-1"))
        self.assertEqual(record["turn_id_sha256"], sha("plain-turn-1"))
        self.assertEqual(record["tool_call_id_sha256"], sha("plain-call-1"))
        self.assertEqual(record["route"], "semble")

    def test_written_fields_are_exactly_the_allowlist(self):
        run = self.run_hook("pre-tool", {"tool_name": "Grep", "tool_input": {"pattern": "x"}})
        self.assertSchemaCompliant(run)
        self.assertEqual(set(run.receipts[0]), set(hook_receipt._SAFE_FIELDS))

    def test_unknown_fields_are_dropped_by_projection(self):
        path = self.root / "explicit.jsonl"
        value = hook_receipt.receipt("PreToolUse", "unknown", tool="Bash", decision="allow")
        value["command"] = "rm -rf /"
        value["prompt"] = "leak me"
        self.assertTrue(hook_receipt.write_receipt(value, path))
        record = json.loads(path.read_text().splitlines()[0])
        self.assertNotIn("command", record)
        self.assertNotIn("prompt", record)
        self.assertNotIn("rm -rf /", path.read_text())

    def test_legacy_and_v16_lines_coexist_in_one_file(self):
        path = self.root / "mixed.jsonl"
        legacy = {"v": 1, "ts": "2026-08-02T00:00:00Z", "event": "PreToolUse",
                  "decision": "deny", "reason": "credential.blocked", "label": "/.ssh/",
                  "cmd_sha256": "f615a34e85e4", "ms": 1}
        path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
        self.assertTrue(hook_receipt.write_receipt(
            hook_receipt.receipt("PreToolUse", "unknown", tool="Bash", decision="deny"), path))
        lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["v"], 1)
        self.assertEqual(lines[1]["schema"], "hook-receipt.v16")
        self.assertNotIn("v", lines[1])


# ---------------------------------------------------------------- 6
class ReceiptHardeningTests(HookTestCase):
    def test_directory_and_file_modes(self):
        run = self.run_hook("pre-tool", {"tool_name": "Grep", "tool_input": {}})
        self.assertSchemaCompliant(run)
        files = sorted(self.receipt_dir.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(self.receipt_dir.lstat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(files[0].lstat().st_mode), 0o600)

    def test_symlinked_receipt_directory_is_refused(self):
        real = self.root / "real-receipts"
        real.mkdir()
        link = self.root / "linked-receipts"
        link.symlink_to(real, target_is_directory=True)
        os.environ["ZGOV_HOOK_SOURCE"] = "test"
        os.environ["ZGOV_HOOK_RECEIPT_DIR"] = str(link)
        self.addCleanup(os.environ.pop, "ZGOV_HOOK_RECEIPT_DIR", None)
        self.addCleanup(os.environ.pop, "ZGOV_HOOK_SOURCE", None)
        value = hook_receipt.receipt("PreToolUse", "unknown", tool="Bash", decision="allow")
        self.assertFalse(hook_receipt.write_receipt(value))
        self.assertEqual(value["receipt_status"], "write_failed")
        self.assertEqual(list(real.iterdir()), [])

    def test_default_receipt_dir_matches_production_path(self):
        self.assertEqual(
            hook_receipt.DEFAULT_RECEIPT_DIR,
            Path.home() / ".zcode" / "hooks" / "receipts",
        )


# ---------------------------------------------------------------- 7
class SensitiveLabelTests(HookTestCase):
    def test_sensitive_labels_are_normalized(self):
        path = self.root / "labels.jsonl"
        value = hook_receipt.receipt("PRIVATE_PROMPT", "PRIVATE_TOKEN", tool="PRIVATE_SECRET")
        self.assertTrue(hook_receipt.write_receipt(value, path))
        record = json.loads(path.read_text().splitlines()[0])
        self.assertEqual(record["event"], "unknown_event")
        self.assertEqual(record["model"], "unknown_model")
        self.assertEqual(record["tool_name"], "unknown_tool")

    def test_governance_reason_codes_are_preserved(self):
        path = self.root / "reasons.jsonl"
        value = hook_receipt.receipt("PreToolUse", "unknown", tool="Bash",
                                     decision="deny", reason_code="credential.blocked")
        self.assertTrue(hook_receipt.write_receipt(value, path))
        record = json.loads(path.read_text().splitlines()[0])
        self.assertEqual(record["reason_code"], "credential.blocked")
        self.assertEqual(record["reason"], "credential.blocked")

    def test_reason_code_charset_is_still_bounded(self):
        path = self.root / "bad-reason.jsonl"
        value = hook_receipt.receipt("PreToolUse", "unknown", tool="Bash",
                                     reason_code="cat /nonexistent-root/user/.ssh/id_rsa # leaked")
        self.assertTrue(hook_receipt.write_receipt(value, path))
        record = json.loads(path.read_text().splitlines()[0])
        self.assertEqual(record["reason_code"], "unspecified_reason")
        self.assertNotIn("id_rsa", path.read_text())


# ---------------------------------------------------------------- 8
class ArgumentIndependenceTests(HookTestCase):
    PAYLOAD = {"tool_name": "Bash", "tool_input": {"command": "git push"}}

    def test_decision_is_stable_and_never_inferred_from_arguments(self):
        first = self.run_hook("pre-tool", self.PAYLOAD)
        second = self.run_hook("pre-tool", self.PAYLOAD)
        self.assertEqual(first.stdout, "")
        self.assertEqual(second.stdout, "")
        self.assertEqual((first.code, second.code), (0, 0))
        decisions = {r["decision"] for r in first.receipts + second.receipts}
        self.assertEqual(decisions, {"allow"})

    def test_policy_decide_ignores_arguments(self):
        base = pre_tool_use_policy.decide("Agent", None)
        for args in ({"subagent_type": "x"}, {"command": "gh pr merge 1"}, {"prompt": "delete everything"}):
            probe = pre_tool_use_policy.decide("Agent", args)
            self.assertEqual(probe["decision"], base["decision"])
            self.assertEqual(probe["reason_code"], base["reason_code"])

    def test_bash_route_only_recognizes_direct_executables(self):
        self.assertEqual(pre_tool_use_policy.route_for("Bash", {"command": "rtk git status"}), "rtk")
        self.assertEqual(pre_tool_use_policy.route_for("Bash", {"command": "rg foo"}), "rg")
        self.assertEqual(pre_tool_use_policy.route_for("Bash", {"command": "rtk ls | head"}), "unspecified")
        self.assertEqual(pre_tool_use_policy.route_for("Bash", {"command": "git push"}), "unspecified")
        self.assertEqual(
            pre_tool_use_policy.route_for("mcp__codegraph__codegraph_explore", {}), "codegraph"
        )
        self.assertEqual(pre_tool_use_policy.route_for("Grep", {}), "rg")


# ---------------------------------------------------------------- 9
class DelegationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CODEX_CONTRACTS.is_dir():
            raise unittest.SkipTest(f"reference contracts absent: {CODEX_CONTRACTS}")

    def packet(self, **overrides):
        value = json.loads((CODEX_CONTRACTS / "delegation_packet.example.json").read_text())
        value.update(overrides)
        return value

    def result(self, **overrides):
        value = json.loads((CODEX_CONTRACTS / "delegation_result.example.json").read_text())
        value.update(overrides)
        return value

    def test_positive_example(self):
        self.assertTrue(validate_packet(self.packet()))
        self.assertTrue(validate_result(self.result(), self.packet()))

    def test_missing_lease(self):
        with self.assertRaises(ContractError) as ctx:
            validate_packet(self.packet(lease={"paths": []}))
        self.assertEqual(str(ctx.exception), "lease")

    def test_counts_arithmetic(self):
        with self.assertRaises(ContractError) as ctx:
            validate_result(self.result(counts={"total": 2, "ran": 1, "passed": 1, "failed": 0, "skipped": 0}),
                            self.packet())
        self.assertEqual(str(ctx.exception), "count arithmetic")

    def test_model_mismatch(self):
        with self.assertRaises(ContractError) as ctx:
            validate_result(self.result(assigned_model="other-model"), self.packet())
        self.assertEqual(str(ctx.exception), "child model/task mismatch")

    def test_empty_model(self):
        with self.assertRaises(ContractError) as ctx:
            validate_packet(self.packet(assigned_model=""))
        self.assertEqual(str(ctx.exception), "model")

    def test_changed_path_outside_lease(self):
        with self.assertRaises(ContractError) as ctx:
            validate_result(self.result(changed_paths=["src/x.py"]), self.packet())
        self.assertEqual(str(ctx.exception), "changed path outside lease")

    def test_retry_overflow(self):
        with self.assertRaises(ContractError) as ctx:
            validate_result(self.result(retry_used=2), self.packet())
        self.assertEqual(str(ctx.exception), "retry overflow")

    def test_contaminated_result(self):
        with self.assertRaises(ContractError) as ctx:
            validate_result(self.result(contamination=True), self.packet())
        self.assertEqual(str(ctx.exception), "contaminated result")

    def test_depth_over_max(self):
        with self.assertRaises(ContractError) as ctx:
            validate_packet(self.packet(depth=2))
        self.assertEqual(str(ctx.exception), "depth")

    def test_forbidden_child_permission(self):
        with self.assertRaises(ContractError) as ctx:
            validate_packet(self.packet(permissions=["read", "merge"]))
        self.assertEqual(str(ctx.exception), "forbidden child permission")


# ---------------------------------------------------------------- 10
class StdlibOnlyTests(HookTestCase):
    HOOK_MODULES = frozenset(
        {"hook_receipt", "pre_tool_use_policy", "session_context", "delegation_contract"}
    )

    def module_roots(self, path: Path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def test_hook_package_imports_only_stdlib_or_siblings(self):
        stdlib = set(sys.stdlib_module_names)
        for path in sorted(HOOK_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                external = self.module_roots(path) - stdlib - self.HOOK_MODULES - {"zgov"}
                self.assertEqual(external, set(), f"{path.name} imports non-stdlib {sorted(external)}")

    def test_zgov_import_is_optional_at_runtime(self):
        staged = self.root / "staged" / "gov" / "hooks"
        staged.parent.mkdir(parents=True)
        shutil.copytree(HOOK_DIR, staged)
        self.assertFalse((staged.parent / "zgov").exists())
        run = self.run_hook(
            "pre-tool",
            {"tool_name": "mcp__semble__search", "tool_input": {"query": "x"}},
            entry=staged / "zcode_hook.py",
        )
        self.assertEqual(run.code, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "")
        self.assertEqual([r["reason_code"] for r in run.receipts], ["routing_module_unavailable"])
        self.assertEqual(run.receipts[0]["route"], "semble")

    def test_zgov_router_is_used_when_available(self):
        self.assertTrue(pre_tool_use_policy.routing_available())

    def test_session_context_degrades_without_zgov(self):
        self.assertIn(session_context.roles_resolved_state(), (True, False, None))


# ---------------------------------------------------------------- 11
class ToolchainProbeTests(HookTestCase):
    """session-start 三工具就绪探测契约：真实探测、有界、只读、缺工具不崩、reason code 复用。"""

    def test_session_start_stdout_schema_survives(self):
        run = self.run_hook("session-start", {"cwd": "/tmp", "session_id": "probe-1"})
        value = self.assertSchemaCompliant(run)  # exit 0 + stdout 键集 ⊆ ALLOWED_KEYS
        self.assertLessEqual(len(value["additionalContext"]), 1200)

    def test_session_start_is_bounded(self):
        t0 = time.monotonic()
        run = self.run_hook("session-start", {"cwd": "/tmp", "session_id": "probe-2"})
        elapsed = time.monotonic() - t0
        self.assertEqual(run.code, 0, run.stderr)
        self.assertLess(elapsed, 10.0, f"session-start 总耗时超界: {elapsed:.2f}s")

    def test_probe_code_never_builds_index(self):
        # 只读不变量：hook 探测代码绝不能自行建索引/同步索引。
        src = (HOOK_DIR / "zcode_hook.py").read_text(encoding="utf-8")
        self.assertNotIn("codegraph init", src)
        self.assertNotIn("codegraph sync", src)

    def test_session_start_survives_missing_tools(self):
        empty_bin = self.root / "empty-bin"
        empty_bin.mkdir()
        env = {"PATH": str(empty_bin) + os.pathsep + "/usr/bin:/bin"}
        run = self.run_hook("session-start", {"cwd": "/tmp", "session_id": "probe-3"}, env=env)
        value = self.assertSchemaCompliant(run)
        ctx = value["additionalContext"]
        self.assertIn("RTK_NOT_FOUND", ctx)
        self.assertIn("CODEGRAPH_NOT_FOUND", ctx)
        self.assertIn("SEMBLE_NOT_FOUND", ctx)
        self.assertIn("工具未就绪", ctx)

    def test_tool_reason_codes_reuse_preflight_literals(self):
        import re as _re
        # 只统计源码里的字符串字面量（reason code 是字面量；RTK_GIT_SUB 等常量名不算）。
        hook_tree = ast.parse((HOOK_DIR / "zcode_hook.py").read_text(encoding="utf-8"))
        used = set()
        for node in ast.walk(hook_tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.update(_re.findall(r"\b((?:RTK|CODEGRAPH|SEMBLE)_[A-Z_]+)\b", node.value))
        preflight_src = (REPO / "gov" / "zgov" / "tool_preflight.py").read_text(encoding="utf-8")
        existing = set(_re.findall(r"\b((?:RTK|CODEGRAPH|SEMBLE)_[A-Z_]+)\b", preflight_src))
        # CODEGRAPH_NOT_INDEXED 是本次新增的"未索引"专用状态：tool_preflight 里
        # 未索引时 index_state 检查只会失败到 CODEGRAPH_INDEX_INVALID，hook 需要
        # 更精确地表达"未建索引"以触发预授权提示，故作为白名单例外。
        # RTK_HEAD_UNBORN_MATCH_SKIPPED 同理：零 commit 仓库（unborn HEAD）里裸
        # `git rev-parse HEAD` 本身 rc≠0，没有可比对基线，hook 跳过第 2 项输出比对
        # 但仍执行第 3 项失败保留检查；tool_preflight 只有 OUTPUT_MATCH/MISMATCH，
        # 没有"无可比对基线"的表达，故一并列入白名单例外。
        whitelist = {"CODEGRAPH_NOT_INDEXED", "RTK_HEAD_UNBORN_MATCH_SKIPPED"}
        novel = used - existing - whitelist
        self.assertEqual(
            novel, set(),
            f"zcode_hook.py 使用了 preflight 不存在的 reason code: {sorted(novel)}",
        )

    def test_probe_rtk_unborn_head_skips_compare_and_is_ready(self):
        # 零 commit 仓库（unborn HEAD）：裸 `git rev-parse HEAD` 本身 rc≠0，没有可比对
        # 基线。rtk 第 2 项应跳过输出比对（reason=RTK_HEAD_UNBORN_MATCH_SKIPPED）而不是
        # 误报 RTK_OUTPUT_MISMATCH；第 1/3 项照常执行，rtk 整体 ready，[工具就绪] 行
        # 显式标注"HEAD未出生跳过输出比对"。
        repo = self.root / "unborn-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        run = self.run_hook("session-start",
                            {"cwd": str(repo), "session_id": "probe-unborn"})
        value = self.assertSchemaCompliant(run)
        ctx = value["additionalContext"]
        self.assertIn("rtk ready(", ctx)
        self.assertIn("HEAD未出生跳过输出比对", ctx)
        self.assertNotIn("RTK_OUTPUT_MISMATCH", ctx)
        reasons = ",".join(r.get("reason") or "" for r in run.receipts)
        # receipt 侧 reason code 统一小写（hook_receipt 规范化）。
        self.assertIn("rtk_head_unborn_match_skipped", reasons)


def _evidence() -> None:
    """Print the raw stdout of every subcommand (acceptance item 1 evidence)."""
    case = StdoutContractTests("test_every_subcommand_emits_only_allowlisted_keys")
    for sub in SUBCOMMANDS:
        case.setUp()
        run = case.run_hook(sub, StdoutContractTests.PAYLOADS[sub])
        print(f"[{sub}] exit={run.code} stdout={run.stdout.strip() or '<empty>'}")
        case.doCleanups()


if __name__ == "__main__":
    if "--evidence" in sys.argv:
        _evidence()
    else:
        unittest.main(verbosity=2)
