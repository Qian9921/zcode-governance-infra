"""Tests for scripts/register-hooks.py.

Every test builds a synthetic config.json inside a tempfile directory.  The
user's real ZCode CLI config is never read or written by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
REGISTER = REPO / "scripts" / "register-hooks.py"
SENTINEL = "SECRET-SENTINEL-VALUE"

FAKE_CONFIG = {
    "provider": {
        "name": "fake-provider",
        "apiKey": SENTINEL,
        "baseUrl": "https://example.invalid/v1",
    },
    "model": "fake-model-1",
    "modelCatalog": [{"id": "fake-model-1", "ctx": 1234}],
    "plugins": {"marketplaces": ["fake-marketplace"], "enabled": ["fake-plugin"]},
    "mcp": {"servers": {"fake-server": {"command": "fake-bin", "args": ["--fake"]}}},
    "hooks": {
        "enabled": False,
        "events": {
            "SessionStart": [
                {"hooks": [{"type": "process", "command": "/bin/true", "args": ["/opt/other.py", "x"]}]}
            ]
        },
    },
}

HOOKS_MANIFEST = {
    "version": "16.0.0",
    "events": {
        "SessionStart": [{"subcommand": "session-start", "timeoutMs": 20000}],
        "PreToolUse": [
            {"matcher": "Bash", "subcommand": "pre-bash", "timeoutMs": 30000},
            {"matcher": "Read|Edit|Write", "subcommand": "pre-file", "timeoutMs": 5000},
        ],
        "PostToolUse": [{"matcher": "Agent", "subcommand": "post-agent", "timeoutMs": 5000}],
    },
}
EXPECTED_ADDED = 4

# Mirrors the shape of the shipped gov/hooks/hooks.json (5 groups), so the
# displacement tests reproduce the real-world removed=3 / added=5 arithmetic.
REAL_SHAPE_MANIFEST = {
    "version": "16.0.0",
    "events": {
        "SessionStart": [{"subcommand": "session-start", "timeoutMs": 20000}],
        "PreToolUse": [
            {"matcher": "Bash", "subcommand": "pre-bash", "timeoutMs": 30000},
            {"matcher": "Read|Edit|Write", "subcommand": "pre-file", "timeoutMs": 5000},
            {"matcher": "Agent|Grep", "subcommand": "pre-agent", "timeoutMs": 5000},
        ],
        "PostToolUse": [{"matcher": "Agent", "subcommand": "post-agent", "timeoutMs": 5000}],
    },
}
REAL_SHAPE_ADDED = 5

# Three incumbent entries pointing at a *different* zcode_hook.py, exactly like
# the pre-existing hooks in a real ~/.zcode/cli/config.json.
INCUMBENT_HOOK = "/incumbent/hooks/zcode_hook.py"
INCUMBENT_SESSION = {
    "hooks": [{"type": "process", "command": "python3", "args": [INCUMBENT_HOOK, "session-start"]}]
}
INCUMBENT_BASH = {
    "matcher": "Bash",
    "hooks": [{"type": "process", "command": "python3", "args": [INCUMBENT_HOOK, "pre-bash"]}],
}
INCUMBENT_FILE = {
    "matcher": "Read|Edit|Write",
    "hooks": [{"type": "process", "command": "python3", "args": [INCUMBENT_HOOK, "pre-file"]}],
}
THIRD_PARTY = {
    "hooks": [{"type": "process", "command": "/bin/true", "args": ["/opt/other.py", "x"]}]
}


def incumbent_config() -> dict:
    cfg = json.loads(json.dumps(FAKE_CONFIG))
    cfg["hooks"] = {
        "enabled": True,
        "events": {
            "SessionStart": [INCUMBENT_SESSION, THIRD_PARTY],
            "PreToolUse": [INCUMBENT_BASH, INCUMBENT_FILE],
        },
    }
    return cfg


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def events_as_multiset(events: dict) -> dict:
    """Per-event sorted canonical entries -- order-insensitive comparison."""
    return {name: sorted(canonical(g) for g in groups) for name, groups in events.items()}


def non_events_snapshot(data: dict) -> dict[str, str]:
    snap: dict[str, str] = {}
    for key, value in data.items():
        if key != "hooks":
            snap[key] = canonical(value)
            continue
        for sub_key, sub_value in value.items():
            if sub_key != "events":
                snap["hooks." + sub_key] = canonical(sub_value)
    return snap


def governance_entry_count(data: dict) -> int:
    total = 0
    for groups in data.get("hooks", {}).get("events", {}).values():
        for group in groups:
            for hook in group.get("hooks", []):
                args = hook.get("args") or []
                if args and pathlib.PurePath(str(args[0])).name == "zcode_hook.py":
                    total += 1
    return total


class Fixture:
    def __init__(
        self,
        tmp: pathlib.Path,
        config: dict | None = None,
        manifest: dict | None = None,
    ) -> None:
        self.root = tmp.resolve()
        self.initial = json.loads(json.dumps(config if config is not None else FAKE_CONFIG))
        self.config = self.root / "cli" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            json.dumps(self.initial, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(self.config, 0o600)
        self.sidecar = self.root / "gov-config" / "displaced-hooks.json"
        self.hook_dir = self.root / "gov" / "hooks"
        self.hook_dir.mkdir(parents=True)
        self.hook_path = self.hook_dir / "zcode_hook.py"
        self.hook_path.write_text("# hook\n", encoding="utf-8")
        (self.hook_dir / "hooks.json").write_text(
            json.dumps(manifest if manifest is not None else HOOKS_MANIFEST, indent=2),
            encoding="utf-8",
        )

    def run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(REGISTER),
                "--config",
                str(self.config),
                "--hook-path",
                str(self.hook_path),
                *args,
            ],
            capture_output=True,
            text=True,
        )

    def load(self) -> dict:
        return json.loads(self.config.read_text(encoding="utf-8"))

    def events(self) -> dict:
        return self.load()["hooks"]["events"]

    def config_sha(self) -> str:
        return hashlib.sha256(self.config.read_bytes()).hexdigest()

    def sidecar_sha(self) -> str | None:
        if not self.sidecar.exists():
            return None
        return hashlib.sha256(self.sidecar.read_bytes()).hexdigest()

    def load_sidecar(self) -> dict:
        return json.loads(self.sidecar.read_text(encoding="utf-8"))


class RegisterTests(unittest.TestCase):
    def test_register_preserves_every_other_key(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))
            before = non_events_snapshot(fx.load())
            before_mode = os.stat(fx.config).st_mode & 0o777

            proc = fx.run()
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "REGISTERED")
            self.assertEqual(payload["added"], EXPECTED_ADDED)
            self.assertEqual(payload["removed"], 0)

            after_raw = fx.load()
            after = non_events_snapshot(after_raw)
            # 1. every non-events key is canonically identical (enabled excluded)
            self.assertEqual(
                {k: v for k, v in before.items() if k != "hooks.enabled"},
                {k: v for k, v in after.items() if k != "hooks.enabled"},
            )
            # 2. hooks.enabled forced true
            self.assertIs(after_raw["hooks"]["enabled"], True)
            # 3. governance entry count correct
            self.assertEqual(governance_entry_count(after_raw), EXPECTED_ADDED)
            # 4. pre-existing foreign hook entry survived
            self.assertIn(
                {"hooks": [{"type": "process", "command": "/bin/true", "args": ["/opt/other.py", "x"]}]},
                after_raw["hooks"]["events"]["SessionStart"],
            )
            # 5. backup written, permissions preserved
            backup = pathlib.Path(payload["config_backup"])
            self.assertTrue(backup.is_file())
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8")),
                json.loads(json.dumps(FAKE_CONFIG)),
            )
            self.assertEqual(os.stat(fx.config).st_mode & 0o777, before_mode)

    def test_registered_entry_shape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))
            self.assertEqual(fx.run().returncode, 0)
            events = fx.load()["hooks"]["events"]
            bash = [
                g for g in events["PreToolUse"] if g.get("matcher") == "Bash"
            ]
            self.assertEqual(len(bash), 1)
            self.assertEqual(
                bash[0]["hooks"][0],
                {
                    "type": "process",
                    "command": "/usr/bin/python3",
                    "args": [str(fx.hook_path), "pre-bash"],
                    "timeoutMs": 30000,
                },
            )

    def test_register_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))
            self.assertEqual(fx.run().returncode, 0)
            first = fx.load()
            second_proc = fx.run()
            self.assertEqual(second_proc.returncode, 0, second_proc.stderr)
            payload = json.loads(second_proc.stdout)
            self.assertEqual(payload["removed"], EXPECTED_ADDED)
            self.assertEqual(payload["added"], EXPECTED_ADDED)
            self.assertEqual(governance_entry_count(fx.load()), EXPECTED_ADDED)
            self.assertEqual(canonical(first), canonical(fx.load()))


class DryRunTests(unittest.TestCase):
    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))
            before = fx.config.read_bytes()
            proc = fx.run("--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "DRY_RUN")
            self.assertEqual(payload["added"], EXPECTED_ADDED)
            self.assertIsNone(payload["config_backup"])
            self.assertEqual(before, fx.config.read_bytes())
            self.assertEqual(list(fx.config.parent.glob("*.zgov-backup-*")), [])


class UnregisterTests(unittest.TestCase):
    def test_unregister_removes_only_governance_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))
            before = non_events_snapshot(fx.load())
            self.assertEqual(fx.run().returncode, 0)

            proc = fx.run("--unregister")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "UNREGISTERED")
            self.assertEqual(payload["removed"], EXPECTED_ADDED)
            self.assertEqual(payload["added"], 0)

            after_raw = fx.load()
            self.assertEqual(governance_entry_count(after_raw), 0)
            after = non_events_snapshot(after_raw)
            self.assertEqual(
                {k: v for k, v in before.items() if k != "hooks.enabled"},
                {k: v for k, v in after.items() if k != "hooks.enabled"},
            )
            self.assertEqual(
                after_raw["hooks"]["events"]["SessionStart"],
                FAKE_CONFIG["hooks"]["events"]["SessionStart"],
            )


class PrivacyTests(unittest.TestCase):
    def test_no_config_value_is_ever_printed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))
            for args in ((), ("--dry-run",), ("--unregister",)):
                with self.subTest(args=args):
                    proc = fx.run(*args)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    combined = proc.stdout + proc.stderr
                    for leaked in (
                        SENTINEL,
                        "fake-provider",
                        "fake-model-1",
                        "fake-marketplace",
                        "fake-server",
                        "https://example.invalid/v1",
                    ):
                        self.assertNotIn(leaked, combined)
            # Key names are still reported.
            payload = json.loads(fx.run("--dry-run").stdout)
            self.assertEqual(
                payload["untouched_top_level_keys"],
                ["hooks.enabled", "mcp", "model", "modelCatalog", "plugins", "provider"],
            )


class FailureTests(unittest.TestCase):
    def test_invalid_json_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))
            fx.config.write_text("{not json", encoding="utf-8")
            proc = fx.run()
            self.assertEqual(proc.returncode, 1)
            self.assertIn("not valid JSON", proc.stderr)
            self.assertEqual(fx.config.read_text(encoding="utf-8"), "{not json")


class DisplacementTests(unittest.TestCase):
    """F1: entries this tool displaces must come back on --unregister."""

    def _incumbent(self, tmp: pathlib.Path) -> Fixture:
        return Fixture(tmp, config=incumbent_config(), manifest=REAL_SHAPE_MANIFEST)

    def test_displaced_incumbents_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = self._incumbent(pathlib.Path(raw))
            before_events = fx.events()
            before_non_events = non_events_snapshot(fx.load())

            reg = fx.run()
            self.assertEqual(reg.returncode, 0, reg.stderr)
            payload = json.loads(reg.stdout)
            self.assertEqual(payload["status"], "REGISTERED")
            self.assertEqual(payload["removed"], 3)
            self.assertEqual(payload["added"], REAL_SHAPE_ADDED)
            self.assertEqual(payload["displaced"], 3)
            self.assertEqual(payload["restored"], 0)
            self.assertEqual(payload["displaced_sidecar"], str(fx.sidecar))

            # sidecar exists, is 0600, and holds the three incumbents verbatim
            self.assertTrue(fx.sidecar.is_file())
            self.assertEqual(os.stat(fx.sidecar).st_mode & 0o777, 0o600)
            side = fx.load_sidecar()
            self.assertEqual(side["schema"], "zgov-displaced-hooks.v1")
            self.assertIs(side["hooks_enabled_before"], True)
            self.assertEqual(
                side["events"],
                {
                    "SessionStart": [INCUMBENT_SESSION],
                    "PreToolUse": [INCUMBENT_BASH, INCUMBENT_FILE],
                },
            )

            unreg = fx.run("--unregister")
            self.assertEqual(unreg.returncode, 0, unreg.stderr)
            payload = json.loads(unreg.stdout)
            self.assertEqual(payload["status"], "UNREGISTERED")
            self.assertEqual(payload["removed"], REAL_SHAPE_ADDED)
            self.assertEqual(payload["restored"], 3)

            after_events = fx.events()
            # Restoration appends, so within an event the order may differ from
            # the original; the set of entries per event must be identical.
            self.assertEqual(
                events_as_multiset(before_events), events_as_multiset(after_events)
            )
            self.assertEqual(sorted(before_events), sorted(after_events))
            # the third-party entry survived the whole round trip
            self.assertIn(THIRD_PARTY, after_events["SessionStart"])
            # every incumbent guard is back
            self.assertIn(INCUMBENT_SESSION, after_events["SessionStart"])
            self.assertIn(INCUMBENT_BASH, after_events["PreToolUse"])
            self.assertIn(INCUMBENT_FILE, after_events["PreToolUse"])
            # sidecar consumed
            self.assertFalse(fx.sidecar.exists())
            # every other key untouched, including hooks.enabled
            self.assertEqual(before_non_events, non_events_snapshot(fx.load()))

    def test_second_register_preserves_original_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = self._incumbent(pathlib.Path(raw))
            before_events = fx.events()
            self.assertEqual(fx.run().returncode, 0)
            first_sidecar_sha = fx.sidecar_sha()

            second = fx.run()
            self.assertEqual(second.returncode, 0, second.stderr)
            payload = json.loads(second.stdout)
            self.assertEqual(payload["displaced_sidecar"], "preserved")
            self.assertEqual(payload["displaced"], 0)
            self.assertEqual(payload["removed"], REAL_SHAPE_ADDED)
            # the first sidecar was NOT clobbered by the second run
            self.assertEqual(fx.sidecar_sha(), first_sidecar_sha)

            unreg = fx.run("--unregister")
            self.assertEqual(unreg.returncode, 0, unreg.stderr)
            self.assertEqual(json.loads(unreg.stdout)["restored"], 3)
            self.assertEqual(
                events_as_multiset(before_events), events_as_multiset(fx.events())
            )
            self.assertFalse(fx.sidecar.exists())

    def test_enabled_false_is_restored_without_displacement(self) -> None:
        """F2: hooks.enabled comes back even when nothing was displaced."""
        with tempfile.TemporaryDirectory() as raw:
            fx = Fixture(pathlib.Path(raw))  # FAKE_CONFIG has enabled=False
            self.assertIs(fx.load()["hooks"]["enabled"], False)

            reg = fx.run()
            self.assertEqual(reg.returncode, 0, reg.stderr)
            payload = json.loads(reg.stdout)
            self.assertEqual(payload["removed"], 0)
            self.assertEqual(payload["displaced"], 0)
            self.assertEqual(payload["displaced_sidecar"], str(fx.sidecar))
            self.assertIs(fx.load()["hooks"]["enabled"], True)
            self.assertTrue(fx.sidecar.is_file())
            self.assertEqual(fx.load_sidecar()["events"], {})
            self.assertIs(fx.load_sidecar()["hooks_enabled_before"], False)

            unreg = fx.run("--unregister")
            self.assertEqual(unreg.returncode, 0, unreg.stderr)
            self.assertEqual(json.loads(unreg.stdout)["restored"], 0)
            self.assertIs(fx.load()["hooks"]["enabled"], False)
            self.assertFalse(fx.sidecar.exists())

    def test_no_displacement_and_already_enabled_writes_no_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cfg = json.loads(json.dumps(FAKE_CONFIG))
            cfg["hooks"] = {"enabled": True, "events": {"SessionStart": [THIRD_PARTY]}}
            fx = Fixture(pathlib.Path(raw), config=cfg)

            payload = json.loads(fx.run().stdout)
            self.assertEqual(payload["removed"], 0)
            self.assertEqual(payload["displaced"], 0)
            self.assertIsNone(payload["displaced_sidecar"])
            self.assertFalse(fx.sidecar.exists())

            payload = json.loads(fx.run("--unregister").stdout)
            self.assertEqual(payload["restored"], 0)
            self.assertIsNone(payload["displaced_sidecar"])
            self.assertFalse(fx.sidecar.exists())
            self.assertEqual(fx.events()["SessionStart"], [THIRD_PARTY])

    def test_dry_run_forecasts_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = self._incumbent(pathlib.Path(raw))
            sha_before = fx.config_sha()

            payload = json.loads(fx.run("--dry-run").stdout)
            self.assertEqual(payload["status"], "DRY_RUN")
            self.assertEqual(payload["removed"], 3)
            self.assertEqual(payload["added"], REAL_SHAPE_ADDED)
            self.assertEqual(payload["displaced"], 3)
            self.assertEqual(fx.config_sha(), sha_before)
            self.assertFalse(fx.sidecar.exists())

            # real register, then a dry-run unregister
            self.assertEqual(fx.run().returncode, 0)
            sha_after_reg = fx.config_sha()
            sidecar_sha = fx.sidecar_sha()

            payload = json.loads(fx.run("--unregister", "--dry-run").stdout)
            self.assertEqual(payload["status"], "DRY_RUN")
            self.assertEqual(payload["restored"], 3)
            self.assertEqual(payload["removed"], REAL_SHAPE_ADDED)
            self.assertEqual(fx.config_sha(), sha_after_reg)
            self.assertEqual(fx.sidecar_sha(), sidecar_sha)

    def test_other_top_level_keys_survive_every_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = self._incumbent(pathlib.Path(raw))
            base = fx.load()
            for key in ("provider", "model", "modelCatalog", "plugins", "mcp"):
                self.assertIn(key, base)
            for args in ((), ("--dry-run",), ("--unregister",), ()):
                with self.subTest(args=args):
                    self.assertEqual(fx.run(*args).returncode, 0)
                    now = fx.load()
                    for key in ("provider", "model", "modelCatalog", "plugins", "mcp"):
                        self.assertEqual(canonical(base[key]), canonical(now[key]))

    def test_sidecar_never_contains_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fx = self._incumbent(pathlib.Path(raw))
            for args in ((), ("--dry-run",), ("--unregister",)):
                with self.subTest(args=args):
                    proc = fx.run(*args)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertNotIn(SENTINEL, proc.stdout + proc.stderr)
            self.assertEqual(fx.run().returncode, 0)
            self.assertTrue(fx.sidecar.is_file())
            self.assertNotIn(SENTINEL, fx.sidecar.read_text(encoding="utf-8"))
            for key in ("provider", "model", "modelCatalog", "plugins", "mcp"):
                self.assertNotIn(key, fx.load_sidecar())


if __name__ == "__main__":
    unittest.main()
