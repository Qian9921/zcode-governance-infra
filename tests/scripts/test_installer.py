"""Safety tests for scripts/install-governance.py.

Every test runs against an isolated tempfile ZCODE_HOME.  The real ~/.zcode is
never read or written by this module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
INSTALLER = REPO / "scripts" / "install-governance.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("zgov_installer", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


installer = _load_installer()


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: pathlib.Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): sha(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


def make_package(tmp: pathlib.Path) -> pathlib.Path:
    """Build a minimal, self-consistent source package with a valid manifest."""
    src = tmp / "package"
    (src / "gov" / "hooks").mkdir(parents=True)
    (src / "gov" / "agents").mkdir(parents=True)
    (src / "gov" / "zgov").mkdir(parents=True)
    (src / "gov" / "AGENTS.md").write_text("# root agents\n", encoding="utf-8")
    (src / "gov" / "agents" / "reviewer.md").write_text("# reviewer\n", encoding="utf-8")
    (src / "gov" / "agents" / "executor.md").write_text("# executor\n", encoding="utf-8")
    (src / "gov" / "hooks" / "zcode_hook.py").write_text("print(1)\n", encoding="utf-8")
    (src / "gov" / "hooks" / "hooks.json").write_text('{"events":{}}\n', encoding="utf-8")
    (src / "gov" / "zgov" / "__init__.py").write_text("", encoding="utf-8")
    (src / "gov" / "roles.example.json").write_text('{"roles":["seed"]}\n', encoding="utf-8")
    (src / "gov" / "milestone.example.json").write_text('{"m":"seed"}\n', encoding="utf-8")
    write_manifest(src)
    return src


def write_manifest(src: pathlib.Path) -> None:
    files = {
        p.relative_to(src).as_posix(): sha(p)
        for p in sorted(src.rglob("*"))
        if p.is_file() and p.name != "manifest.json"
    }
    (src / "manifest.json").write_text(
        json.dumps({"schema_version": "1", "files": files}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def make_zcode_home(tmp: pathlib.Path) -> pathlib.Path:
    """A fake ~/.zcode with the live state that must survive installation."""
    home = tmp / "zcode_home"
    (home / "cli" / "db").mkdir(parents=True)
    (home / "hooks" / "receipts").mkdir(parents=True)
    (home / "server" / "state").mkdir(parents=True)
    (home / "agents").mkdir(parents=True)
    (home / "gov-config").mkdir(parents=True)
    (home / "cli" / "config.json").write_text('{"provider":{"k":"live"}}\n', encoding="utf-8")
    (home / "cli" / "db" / "state.sqlite").write_bytes(b"\x00SQLITE-LIVE")
    (home / "hooks" / "receipts" / "x.jsonl").write_text('{"r":1}\n', encoding="utf-8")
    (home / "server" / "state" / "session.bin").write_bytes(b"SERVER-LIVE")
    (home / "agents" / "existing.md").write_text("# pre-existing agent\n", encoding="utf-8")
    (home / "AGENTS.md").write_text("# pre-existing root AGENTS\n", encoding="utf-8")
    (home / "gov-config" / "roles.json").write_text('{"roles":["USER-OWNED"]}\n', encoding="utf-8")
    return home


def run_installer(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        capture_output=True,
        text=True,
    )


class SafetyGuardTests(unittest.TestCase):
    def test_guard_allows_managed_gov_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = pathlib.Path(raw) / "zcode_home"
            home.mkdir()
            got = installer._assert_safe_destructive_target(home / "gov", home)
            self.assertEqual(got, home / "gov")

    def test_guard_allows_gov_backup_dir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = pathlib.Path(raw) / "zcode_home"
            home.mkdir()
            target = home / ("gov" + installer.BACKUP_SUFFIX)
            self.assertEqual(installer._assert_safe_destructive_target(target, home), target)

    def test_guard_rejects_live_state_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = pathlib.Path(raw) / "zcode_home"
            home.mkdir()
            for bad in (
                home,
                home / "cli",
                home / "hooks",
                home / "server",
                home / "v2",
                home / "gov-config",
                home / "agents",
                pathlib.Path("/"),
                pathlib.Path("/home"),
                home / "gov" / ".." / "cli",
            ):
                with self.subTest(bad=str(bad)):
                    with self.assertRaises(SystemExit):
                        installer._assert_safe_destructive_target(bad, home)

    def test_guard_rejects_relative_and_tmp_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = pathlib.Path(raw) / "zcode_home"
            home.mkdir()
            with self.assertRaises(SystemExit):
                installer._assert_safe_destructive_target(pathlib.Path("gov"), home)
            with self.assertRaises(SystemExit):
                installer._assert_safe_destructive_target(
                    pathlib.Path(tempfile.gettempdir()), home
                )

    def test_guard_allows_paths_inside_system_tempdir(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = pathlib.Path(raw) / "zcode_home"
            home.mkdir()
            scratch = pathlib.Path(tempfile.mkdtemp(prefix=installer.TMP_PREFIX))
            try:
                self.assertEqual(
                    installer._assert_safe_destructive_target(scratch, home), scratch
                )
            finally:
                shutil.rmtree(scratch, ignore_errors=True)


class LiveStatePreservationTests(unittest.TestCase):
    def test_install_leaves_live_state_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            before = {
                name: tree_hashes(home / name)
                for name in ("cli", "hooks", "server")
            }
            before_roles = sha(home / "gov-config" / "roles.json")

            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "READY")

            after = {
                name: tree_hashes(home / name)
                for name in ("cli", "hooks", "server")
            }
            self.assertEqual(before, after, "live ZCODE_HOME state was modified")
            # Pre-existing gov-config files keep their exact bytes; only the
            # absent sibling is seeded.
            self.assertEqual(before_roles, sha(home / "gov-config" / "roles.json"))

    def test_gov_config_user_file_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                (home / "gov-config" / "roles.json").read_text(encoding="utf-8"),
                '{"roles":["USER-OWNED"]}\n',
            )
            # The missing sibling is seeded from the example.
            self.assertTrue((home / "gov-config" / "milestone.json").is_file())
            self.assertEqual(json.loads(proc.stdout)["gov_config"], "created")
            self.assertFalse((home / "gov-config" / "roles.json.zgov-backup").exists())

    def test_gov_config_preserved_when_complete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            (home / "gov-config" / "milestone.json").write_text("{}\n", encoding="utf-8")
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["gov_config"], "preserved")

    def test_sidecar_agents_backed_up_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 0, proc.stderr)

            self.assertEqual(
                (home / "AGENTS.md").read_text(encoding="utf-8"), "# root agents\n"
            )
            self.assertEqual(
                (home / "AGENTS.md.zgov-backup").read_text(encoding="utf-8"),
                "# pre-existing root AGENTS\n",
            )
            self.assertEqual(
                (home / "agents" / "reviewer.md").read_text(encoding="utf-8"), "# reviewer\n"
            )
            self.assertEqual(
                (home / "agents" / "existing.md").read_text(encoding="utf-8"),
                "# pre-existing agent\n",
            )
            payload = json.loads(proc.stdout)
            self.assertEqual(
                payload["sidecar_files"],
                ["AGENTS.md", "agents/executor.md", "agents/reviewer.md"],
            )

    def test_existing_sidecar_agent_gets_backup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            (home / "agents" / "reviewer.md").write_text("# OLD reviewer\n", encoding="utf-8")
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(
                (home / "agents" / "reviewer.md.zgov-backup").read_text(encoding="utf-8"),
                "# OLD reviewer\n",
            )
            self.assertEqual(
                (home / "agents" / "reviewer.md").read_text(encoding="utf-8"), "# reviewer\n"
            )

    def test_managed_gov_dir_populated_with_expected_modes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            gov = home / "gov"
            self.assertTrue((gov / "hooks" / "zcode_hook.py").is_file())
            self.assertEqual(
                os.stat(gov / "hooks" / "hooks.json").st_mode & 0o777, 0o600
            )
            self.assertEqual(
                os.stat(gov / "hooks" / "zcode_hook.py").st_mode & 0o777, 0o644
            )


class DryRunTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            before = tree_hashes(home)
            proc = run_installer(
                "--source", str(src), "--zcode-home", str(home), "--dry-run"
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "DRY_RUN")
            self.assertEqual(payload["destination"], "$ZCODE_HOME")
            self.assertFalse((home / "gov").exists())
            self.assertEqual(before, tree_hashes(home))


class RollbackTests(unittest.TestCase):
    def test_rollback_restores_pre_install_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            (home / "gov").mkdir()
            (home / "gov" / "stale.txt").write_text("OLD GOV\n", encoding="utf-8")
            # gov-config is user-owned and deliberately out of rollback scope.
            def snapshot() -> dict[str, str]:
                return {
                    rel: digest
                    for rel, digest in tree_hashes(home).items()
                    if not rel.startswith("gov-config/")
                }

            before = snapshot()

            install = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(install.returncode, 0, install.stderr)
            self.assertNotEqual(before, snapshot())

            back = run_installer("--zcode-home", str(home), "--rollback")
            self.assertEqual(back.returncode, 0, back.stderr)
            self.assertEqual(json.loads(back.stdout)["status"], "ROLLED_BACK")
            self.assertEqual(before, snapshot())
            # gov-config seeds survive rollback untouched, by design.
            self.assertTrue((home / "gov-config" / "milestone.json").is_file())

    def test_rollback_without_backup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            home = make_zcode_home(pathlib.Path(raw))
            proc = run_installer("--zcode-home", str(home), "--rollback")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("no backup", proc.stderr)


class ManifestIntegrityTests(unittest.TestCase):
    def test_tampered_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            before = tree_hashes(home)
            (src / "gov" / "hooks" / "zcode_hook.py").write_text(
                "print(2)  # tampered\n", encoding="utf-8"
            )
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("manifest mismatch:gov/hooks/zcode_hook.py", proc.stderr)
            self.assertFalse((home / "gov").exists())
            self.assertEqual(before, tree_hashes(home))

    def test_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            before = tree_hashes(home)
            (tmp / "outside.txt").write_text("escape\n", encoding="utf-8")
            manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
            manifest["files"]["gov/../outside.txt"] = sha(tmp / "outside.txt")
            (src / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
            )
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("noncanonical manifest path:gov/../outside.txt", proc.stderr)
            self.assertFalse((home / "gov").exists())
            self.assertEqual(before, tree_hashes(home))

    def test_forbidden_relative_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            src = make_package(tmp)
            home = make_zcode_home(tmp)
            (src / "gov" / "hooks" / "receipts").mkdir()
            (src / "gov" / "hooks" / "receipts" / "leak.jsonl").write_text(
                "{}\n", encoding="utf-8"
            )
            write_manifest(src)
            proc = run_installer("--source", str(src), "--zcode-home", str(home))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("forbidden:", proc.stderr)
            self.assertFalse((home / "gov").exists())


if __name__ == "__main__":
    unittest.main()
