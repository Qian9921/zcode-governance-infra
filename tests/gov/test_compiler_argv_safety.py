"""Compiler argv hard-safety negative/positive cases for zgov."""
from __future__ import annotations

import json
import pathlib
import unittest

from support import mission_with_current_reviewer
from zgov.compiler import CompileError, compile_mission
from zgov.contracts import ContractError


FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "gov" / "zgov" / "fixtures"
VALID_MISSION = json.loads((FIXTURES / "mission.valid.json").read_text(encoding="utf-8"))


def _entrypoint_copy(mission: dict) -> dict:
    """Copy the mission, rebinding the reviewer identity to the live roles config."""
    return mission_with_current_reviewer(mission)


def _set_argv(mission: dict, argv: list[str]) -> dict:
    copy = _entrypoint_copy(mission)
    for entry in copy["entrypoints"]:
        if entry["id"] == "EP-UNIT":
            entry["argv"] = argv
    return copy


def _set_cwd(mission: dict, cwd: str) -> dict:
    copy = _entrypoint_copy(mission)
    for entry in copy["entrypoints"]:
        if entry["id"] == "EP-UNIT":
            entry["cwd"] = cwd
    return copy


class CompilerArgvSafetyTests(unittest.TestCase):

    def test_bash_interpreter_forbidden(self):
        mission = _set_argv(VALID_MISSION, ["bash", "-c", "echo hi"])
        with self.assertRaisesRegex(CompileError, "shell interpreter execution forbidden"):
            compile_mission(mission)

    def test_absolute_bin_sh_forbidden(self):
        mission = _set_argv(VALID_MISSION, ["/bin/sh"])
        with self.assertRaisesRegex(CompileError, "shell interpreter execution forbidden"):
            compile_mission(mission)

    def test_relative_executable_path_forbidden(self):
        mission = _set_argv(VALID_MISSION, ["./python3", "-c", "print()"])
        with self.assertRaisesRegex(CompileError, "relative executable path forbidden"):
            compile_mission(mission)

    def test_shell_metachar_semicolon_forbidden(self):
        mission = _set_argv(VALID_MISSION, ["python3", "-c", "echo; echo"])
        with self.assertRaisesRegex(CompileError, "shell metacharacter execution forbidden"):
            compile_mission(mission)

    def test_shell_metachar_pipe_forbidden(self):
        mission = _set_argv(VALID_MISSION, ["python3", "-c", "echo | cat"])
        with self.assertRaisesRegex(CompileError, "shell metacharacter execution forbidden"):
            compile_mission(mission)

    def test_single_element_whitespace_rejected_by_allowlist(self):
        # ``python3 -c print()`` is one token, so the whole string is the
        # executable and the positive allowlist rejects it first.
        mission = _set_argv(VALID_MISSION, ["python3 -c print()"])
        with self.assertRaisesRegex(CompileError, "executable not in positive allowlist"):
            compile_mission(mission)

    def test_single_element_whitespace_shell_string_forbidden(self):
        # Allowlisted prefix, so this reaches and exercises the dedicated
        # single-element shell-string rule rather than the allowlist rule.
        mission = _set_argv(VALID_MISSION, ["zgov-something arg"])
        with self.assertRaisesRegex(CompileError, "shell-string execution forbidden"):
            compile_mission(mission)

    def test_cwd_traversal_forbidden(self):
        # Rejected by the mission contract layer before the compiler's own argv
        # pass runs, so assert the shared base class (CompileError subclasses it).
        mission = _set_cwd(VALID_MISSION, "../foo")
        with self.assertRaisesRegex(ContractError, "path traversal/empty component forbidden"):
            compile_mission(mission)

    def test_absolute_non_python_interpreter_forbidden(self):
        mission = _set_argv(VALID_MISSION, ["/usr/bin/env", "python3", "-c", "print()"])
        with self.assertRaisesRegex(CompileError, "absolute executable outside Python allowlist"):
            compile_mission(mission)

    def test_python3_positive(self):
        mission = _set_argv(VALID_MISSION, ["python3", "-c", "print()"])
        plan = compile_mission(mission)
        self.assertEqual(plan["execution"], {"shell": False, "background": False, "network": False, "packages": False})

    def test_zgov_prefix_positive(self):
        mission = _set_argv(VALID_MISSION, ["zgov-something", "arg"])
        plan = compile_mission(mission)
        self.assertEqual(plan["execution"], {"shell": False, "background": False, "network": False, "packages": False})


if __name__ == "__main__":
    unittest.main()
