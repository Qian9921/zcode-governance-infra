"""Executed proofs for the zgov.runner sandbox.

Every test compiles a real mission and executes a real gate inside a throwaway
git repository, with the artifact root in a separate temporary tree.  Nothing
touches this repository, the user's home directory, or the network.
"""
import contextlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from support import mission_with_current_reviewer
from zgov import runner
from zgov.compiler import compile_mission

MISSION_FIXTURE = pathlib.Path(runner.__file__).resolve().parent / "fixtures" / "mission.valid.json"

COUNTS_LINE = (
    "import json\n"
    "print(json.dumps({'total': 1, 'ran': 1, 'passed': 1, 'failed': 0,"
    " 'skipped': 0, 'xfail': 0, 'unknown': 0}))\n"
)

ENV_PROBE = (
    "import json, os, sys\n"
    "sys.stderr.write(json.dumps(dict(os.environ)))\n"
) + COUNTS_LINE

SOCKET_PROBE = (
    "import socket, sys\n"
    "try:\n"
    "    socket.socket()\n"
    "except BaseException as exc:\n"
    "    sys.stderr.write('BLOCKED:' + type(exc).__name__ + ':' + str(exc))\n"
    "    raise SystemExit(7)\n"
    "sys.stderr.write('NOT_BLOCKED')\n"
    "raise SystemExit(0)\n"
)

CONNECT_PROBE = (
    "import socket, sys\n"
    "try:\n"
    "    socket.create_connection(('127.0.0.1', 9), 1)\n"
    "except BaseException as exc:\n"
    "    sys.stderr.write('BLOCKED:' + type(exc).__name__ + ':' + str(exc))\n"
    "    raise SystemExit(8)\n"
    "sys.stderr.write('NOT_BLOCKED')\n"
    "raise SystemExit(0)\n"
)

PROCESS_GROUP_PROBE = (
    "import os, subprocess, sys\n"
    "notes = []\n"
    "try:\n"
    "    os.setsid()\n"
    "except BaseException as exc:\n"
    "    notes.append('setsid:' + type(exc).__name__ + ':' + str(exc))\n"
    "try:\n"
    "    subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)\n"
    "except BaseException as exc:\n"
    "    notes.append('popen:' + type(exc).__name__ + ':' + str(exc))\n"
    "sys.stderr.write(chr(10).join(notes))\n"
    "raise SystemExit(9 if len(notes) == 2 else 0)\n"
)

PROXY_KEYS = ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy")


class RunnerSandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.repo_base = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.artifact_base = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))

    @staticmethod
    def _git(root: pathlib.Path, *argv: str) -> str:
        proc = subprocess.run(
            ["git", *argv], cwd=str(root), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        return proc.stdout.strip()

    def make_repo(self, files: dict) -> pathlib.Path:
        root = self.repo_base / "repo"
        root.mkdir()
        self._git(root, "init", "--quiet")
        self._git(root, "config", "user.email", "gate@example.invalid")
        self._git(root, "config", "user.name", "Gate Sandbox")
        self._git(root, "config", "commit.gpgsign", "false")
        for name, body in files.items():
            (root / name).write_text(body, encoding="utf-8")
        self._git(root, "add", "-A")
        self._git(root, "commit", "--quiet", "-m", "sandbox base")
        return root

    def compiled_plan(self, root: pathlib.Path, script: str) -> dict:
        mission = mission_with_current_reviewer(
            json.loads(MISSION_FIXTURE.read_text(encoding="utf-8"))
        )
        head, tree, _ = runner.git_identity(root)
        mission["scope"]["exact_head"] = head
        mission["scope"]["tree_sha"] = tree
        mission["entrypoints"][0]["argv"] = ["python3", script]
        return compile_mission(mission)

    def run_probe(self, script: str, body: str) -> tuple:
        """Execute one real targeted gate and return (result, stdout, stderr)."""
        root = self.make_repo({script: body})
        plan = self.compiled_plan(root, script)
        artifact_dir = self.artifact_base / "artifacts"
        head, tree, _ = runner.git_identity(root)
        gate_runner = runner.GateRunner(root, plan, artifact_dir)
        result = gate_runner.run_gate("G-TARGETED", expected_head=head, expected_tree=tree)
        row = result["rows"][0]
        out = (artifact_dir / row["log_paths"][0]).read_text(encoding="utf-8")
        err = (artifact_dir / row["log_paths"][1]).read_text(encoding="utf-8")
        return result, out, err

    def test_child_environment_is_exactly_the_allowlist(self) -> None:
        result, _, err = self.run_probe("probe_env.py", ENV_PROBE)
        row = result["rows"][0]
        self.assertEqual(row["exit_status"], 0)
        self.assertEqual(row["parse_error"], "")
        env = json.loads(err)

        expected_keys = {
            "PATH", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
            "HOME", "ZCODE_HOME", "PYTHONPATH",
        } | ({"LANG", "LC_ALL", "TZ"} & set(os.environ))
        self.assertEqual(set(env), expected_keys)

        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")
        self.assertNotEqual(env["PATH"], os.environ.get("PATH"))
        for key in PROXY_KEYS:
            self.assertNotIn(key, env)
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

        artifact_dir = (self.artifact_base / "artifacts").resolve()
        self.assertNotIn("CODEX_HOME", env)
        zcode_home = pathlib.Path(env["ZCODE_HOME"])
        self.assertEqual(zcode_home.parent, artifact_dir)
        self.assertNotEqual(zcode_home, pathlib.Path.home() / ".zcode")
        self.assertFalse(str(zcode_home).startswith(str(pathlib.Path.home() / ".zcode")))
        self.assertEqual(pathlib.Path(env["HOME"]).parent, artifact_dir)
        self.assertNotEqual(pathlib.Path(env["HOME"]), pathlib.Path.home())

    def test_socket_construction_is_denied_offline(self) -> None:
        result, _, err = self.run_probe("probe_socket.py", SOCKET_PROBE)
        row = result["rows"][0]
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(row["decision"], "deny")
        self.assertEqual(row["exit_status"], 7)
        # A refused connection would be an OSError; only sitecustomize raises
        # this RuntimeError, so the denial cannot be a network artifact.
        self.assertEqual(err, "BLOCKED:RuntimeError:offline socket denied")

    def test_create_connection_is_denied_offline(self) -> None:
        result, _, err = self.run_probe("probe_connect.py", CONNECT_PROBE)
        row = result["rows"][0]
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(row["exit_status"], 8)
        self.assertEqual(err, "BLOCKED:RuntimeError:offline network denied")

    def test_process_group_escape_is_denied(self) -> None:
        result, _, err = self.run_probe("probe_pgroup.py", PROCESS_GROUP_PROBE)
        row = result["rows"][0]
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(row["exit_status"], 9)
        self.assertEqual(
            err.splitlines(),
            [
                "setsid:RuntimeError:process-group escape denied",
                "popen:RuntimeError:process-group escape denied",
            ],
        )

    def test_artifact_root_inside_repository_is_rejected(self) -> None:
        root = self.make_repo({"probe.py": COUNTS_LINE})
        plan = self.compiled_plan(root, "probe.py")
        with self.assertRaises(runner.GateRunError) as ctx:
            runner.GateRunner(root, plan, root / "artifacts")
        self.assertEqual(str(ctx.exception), "artifact root must be outside the repository")

    def test_artifact_root_symlink_is_rejected(self) -> None:
        root = self.make_repo({"probe.py": COUNTS_LINE})
        plan = self.compiled_plan(root, "probe.py")
        target = self.artifact_base / "real"
        target.mkdir()
        link = self.artifact_base / "link"
        link.symlink_to(target)
        with self.assertRaises(runner.GateRunError) as ctx:
            runner.GateRunner(root, plan, link)
        self.assertEqual(str(ctx.exception), "artifact root symlink forbidden")

    def test_candidate_and_final_reject_a_dirty_worktree(self) -> None:
        root = self.make_repo({"probe.py": COUNTS_LINE})
        plan = self.compiled_plan(root, "probe.py")
        head, _, _ = runner.git_identity(root)
        (root / "dirt.txt").write_text("uncommitted\n", encoding="utf-8")
        self.assertTrue(runner.git_identity(root)[2])
        for tier in ("CANDIDATE", "FINAL"):
            gate_runner = runner.GateRunner(root, plan, self.artifact_base / f"artifacts-{tier}")
            with self.assertRaises(runner.GateRunError) as ctx:
                gate_runner.run_tier(tier, expected_head=head)
            self.assertEqual(str(ctx.exception), "CANDIDATE/FINAL require the exact clean candidate")

    def test_only_fast_accepts_a_mutable_snapshot(self) -> None:
        root = self.make_repo({"probe.py": COUNTS_LINE})
        plan = self.compiled_plan(root, "probe.py")
        head, _, _ = runner.git_identity(root)
        (root / "dirt.txt").write_text("uncommitted\n", encoding="utf-8")
        gate_runner = runner.GateRunner(root, plan, self.artifact_base / "artifacts-mutable")
        with self.assertRaises(runner.GateRunError) as ctx:
            gate_runner.run_tier("CANDIDATE", expected_head=head, snapshot_mode="worktree")
        self.assertEqual(str(ctx.exception), "only FAST accepts a mutable snapshot")

        snapshot = runner.content_snapshot(root, "worktree")
        fast_runner = runner.GateRunner(root, plan, self.artifact_base / "artifacts-fast")
        run = fast_runner.run_tier("FAST", expected_head=head, snapshot_mode="worktree")
        self.assertEqual([r["gate_id"] for r in run["results"]], ["G-TARGETED"])
        self.assertEqual(run["results"][0]["decision"], "allow")
        self.assertEqual(run["results"][0]["snapshot_sha256"], snapshot)
        self.assertEqual(run["results"][0]["snapshot_mode"], "worktree")

    def test_max_workers_bounds(self) -> None:
        root = self.make_repo({"probe.py": COUNTS_LINE})
        plan = self.compiled_plan(root, "probe.py")
        for workers in (0, 17, -1, 1.0, True):
            with self.assertRaises(runner.GateRunError) as ctx:
                runner.GateRunner(root, plan, self.artifact_base / "rejected", max_workers=workers)
            self.assertEqual(str(ctx.exception), "max_workers must be an integer in [1,16]")
        for workers in (1, 16):
            accepted = runner.GateRunner(
                root, plan, self.artifact_base / f"accepted-{workers}", max_workers=workers,
            )
            self.assertEqual(accepted.max_workers, workers)


if __name__ == "__main__":
    unittest.main()
