#!/usr/bin/env python3
"""Install the ZCode governance package into $ZCODE_HOME.

Safety contract (differs from the Codex original by design):

* ``$ZCODE_HOME`` itself is a live directory (``cli/`` holds config + databases,
  ``hooks/receipts/`` holds append-only receipts, ``server/`` and ``v2/`` hold
  runtime state).  It is therefore NEVER renamed, replaced or removed.
* Exactly one directory is "managed" and replaced atomically:
  ``$ZCODE_HOME/gov``.
* Everything outside ``$ZCODE_HOME/gov`` is written file-by-file, and every
  pre-existing file is copied to ``<name>.zgov-backup`` first.
* ``$ZCODE_HOME/gov-config/`` is user-owned: files are created only when
  absent and are never overwritten, backed up or otherwise touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import tempfile

FORBIDDEN = (
    "sessions",
    "receipts",
    "plugins",
    "connections",
    "models_cache.json",
    ".env",
    "config.json",
)
BACKUP_SUFFIX = ".zgov-backup"
TMP_PREFIX = "zgov-install-"
MANAGED_DIRNAME = "gov"
SIDECAR_AGENTS_MD = "AGENTS.md"
SIDECAR_AGENTS_DIR = "agents"
GOV_CONFIG_DIRNAME = "gov-config"
GOV_CONFIG_SEEDS = (
    ("roles.example.json", "roles.json"),
    ("milestone.example.json", "milestone.json"),
)


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _assert_safe_destructive_target(path: pathlib.Path, zcode_home: pathlib.Path) -> pathlib.Path:
    """Allow destructive operations only on the managed dir or a temp dir.

    Raises SystemExit for anything else.  Every rmtree/rename in this module
    must route through this guard.
    """
    candidate = pathlib.Path(path)
    if not candidate.is_absolute():
        raise SystemExit("unsafe destructive target:" + str(candidate))
    candidate = pathlib.Path(os.path.normpath(str(candidate)))
    home = pathlib.Path(os.path.normpath(str(pathlib.Path(zcode_home).absolute())))
    managed = home / MANAGED_DIRNAME
    managed_backup = home / (MANAGED_DIRNAME + BACKUP_SUFFIX)
    if candidate in (managed, managed_backup):
        return candidate
    # The only other allowed targets are staging directories this installer
    # created itself: inside the system temp dir AND named with TMP_PREFIX.
    tmp_root = pathlib.Path(os.path.normpath(tempfile.gettempdir()))
    try:
        rel = candidate.relative_to(tmp_root)
    except ValueError:
        raise SystemExit("unsafe destructive target:" + str(candidate))
    if not rel.parts or not rel.parts[0].startswith(TMP_PREFIX):
        raise SystemExit("unsafe destructive target:" + str(candidate))
    return candidate


def _safe_rmtree(path: pathlib.Path, zcode_home: pathlib.Path) -> None:
    target = _assert_safe_destructive_target(path, zcode_home)
    shutil.rmtree(target, ignore_errors=False)


def _safe_rename(src: pathlib.Path, dst: pathlib.Path, zcode_home: pathlib.Path) -> None:
    _assert_safe_destructive_target(src, zcode_home)
    _assert_safe_destructive_target(dst, zcode_home)
    src.rename(dst)


def collect(src: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """Return [(rel-inside-gov, source path)] validated against manifest.json."""
    package = src / "gov"
    if not package.is_dir():
        raise SystemExit("missing gov package")
    package_root = package.resolve(strict=True)
    try:
        declared = json.loads((src / "manifest.json").read_text(encoding="utf-8"))["files"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise SystemExit("invalid manifest")
    if not isinstance(declared, dict):
        raise SystemExit("invalid manifest files")
    out: list[tuple[str, pathlib.Path]] = []
    for source_rel, expected_hash in sorted(declared.items()):
        if not isinstance(source_rel, str) or not source_rel.startswith("gov/"):
            continue
        parts = source_rel.split("/")
        if (
            "\\" in source_rel
            or len(parts) < 2
            or parts[0] != "gov"
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise SystemExit("noncanonical manifest path:" + source_rel)
        rel = "/".join(parts[1:])
        p = package.joinpath(*parts[1:])
        try:
            resolved = p.resolve(strict=True)
            resolved.relative_to(package_root)
        except (FileNotFoundError, RuntimeError, ValueError):
            raise SystemExit("source escape:" + source_rel)
        if not p.is_file() or p.is_symlink() or sha(p) != expected_hash:
            raise SystemExit("manifest mismatch:" + source_rel)
        out.append((rel, p))
    if not out:
        raise SystemExit("empty gov package")
    return out


def _split_entries(
    entries: list[tuple[str, pathlib.Path]],
) -> tuple[list[tuple[str, pathlib.Path]], list[tuple[str, pathlib.Path]]]:
    """Split into (managed, sidecar).

    Sidecars are files that must also land outside ``$ZCODE_HOME/gov``:
    ``AGENTS.md`` and ``agents/*.md``.  They stay in the managed copy too.
    """
    sidecars: list[tuple[str, pathlib.Path]] = []
    for rel, p in entries:
        parts = rel.split("/")
        if rel == SIDECAR_AGENTS_MD:
            sidecars.append((SIDECAR_AGENTS_MD, p))
        elif len(parts) == 2 and parts[0] == SIDECAR_AGENTS_DIR and parts[1].endswith(".md"):
            sidecars.append((rel, p))
    return entries, sidecars


def _backup_file(path: pathlib.Path) -> pathlib.Path | None:
    if not path.exists():
        return None
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    shutil.copy2(path, backup)
    return backup


def _write_mode(path: pathlib.Path) -> int:
    return 0o600 if path.suffix == ".json" else 0o644


def _seed_gov_config(src: pathlib.Path, zcode_home: pathlib.Path, dry_run: bool) -> str:
    """Create gov-config/*.json only when absent.  Never overwrite."""
    target_dir = zcode_home / GOV_CONFIG_DIRNAME
    missing: list[tuple[pathlib.Path, pathlib.Path]] = []
    for example_name, final_name in GOV_CONFIG_SEEDS:
        example = src / "gov" / example_name
        final = target_dir / final_name
        if final.exists():
            continue
        if not example.is_file():
            raise SystemExit("missing gov-config seed:gov/" + example_name)
        missing.append((example, final))
    if not missing:
        return "preserved"
    if dry_run:
        return "created"
    target_dir.mkdir(parents=True, exist_ok=True)
    for example, final in missing:
        shutil.copy2(example, final)
        os.chmod(final, 0o600)
    return "created"


def _installed_sidecar_rels(zcode_home: pathlib.Path) -> list[str]:
    """Sidecars this installer wrote, derived from the installed managed tree."""
    managed = zcode_home / MANAGED_DIRNAME
    rels: list[str] = []
    if (managed / SIDECAR_AGENTS_MD).is_file():
        rels.append(SIDECAR_AGENTS_MD)
    agents_dir = managed / SIDECAR_AGENTS_DIR
    if agents_dir.is_dir():
        rels.extend(
            SIDECAR_AGENTS_DIR + "/" + q.name
            for q in sorted(agents_dir.glob("*.md"))
            if q.is_file()
        )
    return rels


def _rollback(zcode_home: pathlib.Path) -> int:
    managed = zcode_home / MANAGED_DIRNAME
    managed_backup = zcode_home / (MANAGED_DIRNAME + BACKUP_SUFFIX)
    # Derive the sidecar set from the currently installed tree, before the
    # managed directory is swapped back.
    sidecar_rels = _installed_sidecar_rels(zcode_home)
    if not managed_backup.exists() and not sidecar_rels:
        raise SystemExit("no backup")

    reverted: list[str] = []
    for rel in sidecar_rels:
        target = zcode_home.joinpath(*rel.split("/"))
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        if backup.is_file():
            shutil.copy2(backup, target)
            backup.unlink()
            reverted.append(rel)
        elif target.is_file():
            # Newly created by the installer: exact rollback removes it.
            target.unlink()
            reverted.append(rel)

    if managed_backup.exists():
        if managed.exists():
            _safe_rmtree(managed, zcode_home)
        _safe_rename(managed_backup, managed, zcode_home)
    elif managed.exists():
        _safe_rmtree(managed, zcode_home)

    print(
        json.dumps(
            {
                "status": "ROLLED_BACK",
                "managed_files": 0,
                "sidecar_files": sorted(reverted),
                "gov_config": "preserved",
                "destination": str(zcode_home),
                "hashes": {},
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=".")
    ap.add_argument("--zcode-home", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args(argv)
    src = pathlib.Path(a.source).resolve()
    zcode_home = pathlib.Path(a.zcode_home).expanduser().resolve()
    managed = zcode_home / MANAGED_DIRNAME
    managed_backup = zcode_home / (MANAGED_DIRNAME + BACKUP_SUFFIX)

    if a.rollback:
        return _rollback(zcode_home)

    entries = collect(src)
    bad = [r for r, _ in entries if any(x in r for x in FORBIDDEN)]
    if bad:
        raise SystemExit("forbidden:" + ",".join(bad))
    entries, sidecars = _split_entries(entries)
    gov_config = _seed_gov_config(src, zcode_home, dry_run=True) if a.dry_run else None

    payload = {
        "status": "DRY_RUN" if a.dry_run else "READY",
        "managed_files": len(entries),
        "sidecar_files": sorted(rel for rel, _ in sidecars),
        "gov_config": gov_config if a.dry_run else "",
        "destination": "$ZCODE_HOME" if a.dry_run else str(zcode_home),
        "hashes": {r: sha(p) for r, p in entries},
    }
    if a.dry_run:
        print(json.dumps(payload, sort_keys=True))
        return 0

    if not zcode_home.is_dir():
        raise SystemExit("zcode home is not a directory:" + str(zcode_home))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix=TMP_PREFIX))
    tmp_resolved = tmp.resolve(strict=True)
    try:
        for rel, p in entries:
            q = tmp.joinpath(*rel.split("/"))
            try:
                q.resolve(strict=False).relative_to(tmp_resolved)
            except (RuntimeError, ValueError):
                raise SystemExit("destination escape:" + rel)
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, q)
            os.chmod(q, _write_mode(q))
        # Atomic swap of the ONLY managed directory.
        if managed_backup.exists():
            _safe_rmtree(managed_backup, zcode_home)
        if managed.exists():
            _safe_rename(managed, managed_backup, zcode_home)
        _assert_safe_destructive_target(tmp_resolved, zcode_home)
        try:
            _safe_rename(tmp_resolved, managed, zcode_home)
        except OSError:
            shutil.copytree(tmp_resolved, managed)
            _safe_rmtree(tmp_resolved, zcode_home)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    # Sidecars: per-file backup then per-file write.  No directory replacement.
    for rel, p in sidecars:
        target = zcode_home.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        _backup_file(target)
        shutil.copy2(p, target)
        os.chmod(target, _write_mode(target))

    payload["gov_config"] = _seed_gov_config(src, zcode_home, dry_run=False)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
