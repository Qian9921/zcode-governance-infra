"""Repo hygiene guard: the tracked repository never carries private identifiers.

This is the tripwire for anyone re-committing a private value after the repo
goes public.  It scans every tracked file (the same ``git ls-files`` file set
the manifest generator uses) and fails on any occurrence of:

- the private provider/model identifiers that used to ship as defaults;
- UUID-shaped provider ids;
- the two private GitHub usernames that used to be hard-coded.

``gov/zgov/fixtures/examples/`` is exempt: it is a byte-for-byte copy of the
upstream Codex reference artifacts whose sha256 is locked by other tests.

The forbidden substrings below are intentionally assembled from fragments so
this test's own source never contains the full literal strings (the repo-hygiene
and the acceptance grep must stay clean even after this file is tracked).
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
EXEMPT_DIRS = (".git", "__pycache__", ".pytest_cache", ".codegraph", ".venv")
EXEMPT_PREFIXES = ("gov/zgov/fixtures/examples/",)
SKIP_FILES = {"manifest.json"}

_DASH = "-"
_PRIVATE_MODEL = "|".join(
    ("tuzi" + _DASH + "direct", "claude" + _DASH + "tuzi", "deepseek" + _DASH + "v4")
)
_UUID = (
    r"[0-9a-f]{8}" + _DASH + r"[0-9a-f]{4}" + _DASH + r"[0-9a-f]{4}" + _DASH
    + r"[0-9a-f]{4}" + _DASH + r"[0-9a-f]{12}"
)
_PRIVATE_LOGIN = "|".join(("Qian" + "9921", "Liang" + "9921"))

FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("private model/provider identifier", re.compile(_PRIVATE_MODEL)),
    ("UUID-shaped provider id", re.compile(_UUID)),
    ("private GitHub login", re.compile(_PRIVATE_LOGIN)),
)


def tracked_files() -> list[str]:
    """Return the tracked file set (git ls-files), with a walk fallback."""
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=str(REPO), stderr=subprocess.DEVNULL
        )
        paths = [item.decode("utf-8") for item in output.split(b"\0") if item]
    except (OSError, subprocess.CalledProcessError):
        paths = []
    if not paths:
        for path in REPO.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO)
            if any(part in EXEMPT_DIRS for part in rel.parts):
                continue
            paths.append(rel.as_posix())
    return sorted(path for path in paths if path not in SKIP_FILES)


class RepoHygieneTests(unittest.TestCase):
    def test_tracked_repo_has_no_private_identifiers(self) -> None:
        violations: list[str] = []
        files = tracked_files()
        self.assertTrue(files, "repo file set must not be empty")
        for rel in files:
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
            for label, pattern in FORBIDDEN_PATTERNS:
                if pattern.search(text):
                    violations.append(f"{rel}: {label}")
        self.assertEqual(
            violations,
            [],
            "tracked repository must not carry private identifiers:\n"
            + "\n".join(f"- {v}" for v in violations),
        )

    def test_examples_reference_artifacts_are_tracked_and_exempt(self) -> None:
        """The exempt prefix exists and is under git, so the exclusion is real."""
        examples = [
            f for f in tracked_files() if f.startswith("gov/zgov/fixtures/examples/")
        ]
        self.assertGreaterEqual(len(examples), 10)


if __name__ == "__main__":
    unittest.main()
