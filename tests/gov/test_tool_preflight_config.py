"""Tests for ZCode-specific configuration probing in tool_preflight."""

from __future__ import annotations

import pathlib
import subprocess
import tempfile
import unittest
from collections.abc import Sequence

from zgov import tool_preflight as tp


class ZcodeMcpServersTest(unittest.TestCase):
    """_zcode_mcp_servers parses several JSON layouts fail-closed."""

    def test_mcp_servers_object(self) -> None:
        text = '{"mcp":{"servers":{"codegraph":{},"semble":{}}}}'
        self.assertEqual(tp._zcode_mcp_servers(text), {"codegraph", "semble"})

    def test_mcp_mcpservers_object(self) -> None:
        text = '{"mcp":{"mcpServers":{"codegraph":{},"semble":{}}}}'
        self.assertEqual(tp._zcode_mcp_servers(text), {"codegraph", "semble"})

    def test_mcp_dict_of_dicts(self) -> None:
        text = '{"mcp":{"codegraph":{},"semble":{}}}'
        self.assertEqual(tp._zcode_mcp_servers(text), {"codegraph", "semble"})

    def test_top_level_mcpservers(self) -> None:
        text = '{"mcpServers":{"codegraph":{},"semble":{}}}'
        self.assertEqual(tp._zcode_mcp_servers(text), {"codegraph", "semble"})

    def test_only_codegraph_excludes_scalar_enabled(self) -> None:
        text = '{"mcp":{"enabled":true,"servers":{"codegraph":{}}}}'
        self.assertEqual(tp._zcode_mcp_servers(text), {"codegraph"})

    def test_flat_mcp_excludes_scalar_enabled(self) -> None:
        text = '{"mcp":{"enabled":true,"codegraph":{}}}'
        self.assertEqual(tp._zcode_mcp_servers(text), {"codegraph"})

    def test_invalid_json_returns_empty(self) -> None:
        self.assertEqual(tp._zcode_mcp_servers("not json"), set())

    def test_empty_object_returns_empty(self) -> None:
        self.assertEqual(tp._zcode_mcp_servers("{}"), set())


class ServerConfiguredTest(unittest.TestCase):
    """_server_configured does case-insensitive substring matching."""

    def test_exact_match(self) -> None:
        self.assertTrue(tp._server_configured({"codegraph"}, "codegraph"))

    def test_prefix_suffix_match(self) -> None:
        self.assertTrue(tp._server_configured({"mcp__codegraph__codegraph"}, "codegraph"))

    def test_case_insensitive(self) -> None:
        self.assertTrue(tp._server_configured({"CodeGraph"}, "codegraph"))

    def test_missing(self) -> None:
        self.assertFalse(tp._server_configured({"rtk"}, "codegraph"))


class RunPreflightReadOnlyTest(unittest.TestCase):
    """run_preflight with a fake runner produces a read-only report."""

    def test_mutations_empty_and_denominator_known(self) -> None:
        head_sha = "a" * 40

        def fake_runner(
            argv: Sequence[str], cwd: pathlib.Path, timeout_sec: float
        ) -> subprocess.CompletedProcess[str]:
            if list(argv) == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(argv, 0, head_sha + "\n", "")
            if list(argv) == ["git", "status", "--porcelain=v1", "-z"]:
                return subprocess.CompletedProcess(argv, 0, "", "")
            if list(argv) == [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ]:
                return subprocess.CompletedProcess(argv, 0, "expected.txt\0", "")
            return subprocess.CompletedProcess(argv, 1, "", "unexpected command")

        def fake_which(name: str) -> str | None:
            return None

        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            config_path = repo / "config.json"
            config_path.write_text(
                '{"mcp":{"servers":{"codegraph":{},"semble":{}}}}',
                encoding="utf-8",
            )
            (repo / "expected.txt").write_text("sentinel", encoding="utf-8")

            report = tp.run_preflight(
                repo,
                semantic_query="find expected",
                expected_path="expected.txt",
                config_path=config_path,
                strict=True,
                runner=fake_runner,
                which=fake_which,
            )

        self.assertEqual(report["mutations"], [])
        self.assertEqual(report["denominator"], 3)
        self.assertIs(report["denominator_known"], True)
        counts = report["counts"]
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(counts["xfail"], 0)
        self.assertEqual(counts["unknown"], 0)
        for tool in report["tools"]:
            self.assertIn(tool["tool"], tp.MANDATORY_TOOLS)


class CacheInvalidatorTest(unittest.TestCase):
    """cache.invalidated_by contains the renamed ZCode invalidator."""

    def test_invalidated_by_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            (repo / "expected.txt").write_text("sentinel", encoding="utf-8")
            # Initialize a real git repository so git identity checks pass.
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "add", "expected.txt"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            config_path = repo / "config.json"
            config_path.write_text("{}", encoding="utf-8")

            report = tp.run_preflight(
                repo,
                semantic_query="find expected",
                expected_path="expected.txt",
                config_path=config_path,
                strict=False,
            )

        invalidated_by = report["cache"]["invalidated_by"]
        self.assertEqual(len(invalidated_by), 10)
        self.assertIn("zcode_config_change", invalidated_by)
        self.assertNotIn("codex_config_change", invalidated_by)


if __name__ == "__main__":
    unittest.main()
