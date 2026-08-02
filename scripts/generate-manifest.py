#!/usr/bin/env python3
"""Regenerate manifest.json from the on-disk package tree.

Enumeration mirrors scripts/verify-governance.py exactly: ``git ls-files -z``
when it yields anything, otherwise an ``rglob`` walk with explicit exclusions.
Sharing the file set is what keeps the verifier's "manifest missing/extra"
check meaningful instead of self-fulfilling.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess

SCHEMA_VERSION = "1"
VERSION = "16.0.0"
PACKAGE = "zcode-governance-infra"
ALLOWLIST = [
    "gov/",
    "scripts/",
    "docs/",
    "tests/",
    "README.md",
    "README.zh-CN.md",
    "SECURITY.md",
    "PRIVACY.md",
    "LICENSE",
    "AGENTS.md",
    "manifest.json",
]
FORBIDDEN = [
    "sessions",
    "receipts",
    "plugins",
    "connections",
    "models_cache.json",
    ".env",
    "token",
    "credential",
    "prompt",
    "transcript",
]
EXCLUDED_DIR_PARTS = (".git", "__pycache__", ".venv", ".codegraph", ".artifacts")
EXCLUDED_NAMES = ("manifest.json", ".DS_Store")
EXCLUDED_SUFFIXES = (".pyc",)


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def enumerate_files(root: pathlib.Path) -> list[str]:
    try:
        output = subprocess.check_output(["git", "ls-files", "-z"], cwd=str(root))
        tracked = [
            item.decode("utf-8")
            for item in output.split(b"\0")
            if item and item.decode("utf-8") != "manifest.json"
        ]
        if tracked:
            return sorted(tracked)
    except (OSError, subprocess.CalledProcessError):
        pass
    out: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if any(part in EXCLUDED_DIR_PARTS for part in parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES:
            continue
        out.append(rel)
    return sorted(out)


def build(root: pathlib.Path) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "package": PACKAGE,
        "allowlist": ALLOWLIST,
        "forbidden": FORBIDDEN,
        "files": {rel: sha(root / rel) for rel in enumerate_files(root)},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--check", action="store_true", help="do not write; report drift")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.repo).resolve()
    manifest = build(root)
    target = root / "manifest.json"
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        drift = current != text
        print(json.dumps({"status": "DRIFT" if drift else "CLEAN", "files": len(manifest["files"])}, sort_keys=True))
        return 1 if drift else 0
    target.write_text(text, encoding="utf-8")
    print(json.dumps({"status": "WRITTEN", "files": len(manifest["files"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
