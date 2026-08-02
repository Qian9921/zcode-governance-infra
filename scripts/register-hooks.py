#!/usr/bin/env python3
"""Narrow, reversible merger for governance hooks into ZCode's cli/config.json.

Only entries under ``hooks.events`` whose ``args[0]`` points at ``zcode_hook.py``
are added or removed.  Every other key in the config -- including ``provider``,
``model``, ``modelCatalog``, ``plugins``, ``mcp`` and any non-``events`` key
under ``hooks`` -- is preserved byte-for-byte and is verified after the write.

Privacy: no config *value* is ever printed.  Only key names and counts.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile

HOOK_ENTRY_BASENAME = "zcode_hook.py"
BACKUP_PREFIX = ".zgov-backup-"
DEFAULT_PYTHON = "/usr/bin/python3"
SIDECAR_DIRNAME = "gov-config"
SIDECAR_FILENAME = "displaced-hooks.json"
SIDECAR_SCHEMA = "zgov-displaced-hooks.v1"


def _default_zcode_home() -> pathlib.Path:
    return pathlib.Path(
        os.environ.get("ZCODE_HOME") or (pathlib.Path.home() / ".zcode")
    ).expanduser()


def _default_config_path() -> pathlib.Path:
    return _default_zcode_home() / "cli" / "config.json"


def _default_hook_path() -> pathlib.Path:
    return _default_zcode_home() / "gov" / "hooks" / HOOK_ENTRY_BASENAME


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load_config(path: pathlib.Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit("cannot read config:" + type(exc).__name__)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit("config is not valid JSON")
    if not isinstance(data, dict):
        raise SystemExit("config root must be an object")
    return data


def _load_hook_manifest(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SystemExit("cannot read hooks manifest:" + str(path))
    events = data.get("events")
    if not isinstance(events, dict):
        raise SystemExit("hooks manifest has no events object")
    return events


def _is_governance_entry(group: object, hook_path: str) -> bool:
    """True when a hooks.events group contains our zcode_hook.py invocation."""
    if not isinstance(group, dict):
        return False
    hooks = group.get("hooks")
    if not isinstance(hooks, list):
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        args = hook.get("args")
        if not isinstance(args, list) or not args:
            continue
        first = args[0]
        if not isinstance(first, str):
            continue
        if first == hook_path or pathlib.PurePath(first).name == HOOK_ENTRY_BASENAME:
            return True
    return False


def _build_groups(events: dict, hook_path: str, python: str) -> dict[str, list[dict]]:
    built: dict[str, list[dict]] = {}
    for event_name in sorted(events):
        specs = events[event_name]
        if not isinstance(specs, list):
            raise SystemExit("hooks manifest event is not a list:" + event_name)
        groups: list[dict] = []
        for spec in specs:
            if not isinstance(spec, dict):
                raise SystemExit("hooks manifest entry is not an object:" + event_name)
            subcommand = spec.get("subcommand")
            if not isinstance(subcommand, str) or not subcommand:
                raise SystemExit("hooks manifest entry missing subcommand:" + event_name)
            hook: dict = {
                "type": "process",
                "command": python,
                "args": [hook_path, subcommand],
            }
            timeout = spec.get("timeoutMs")
            if isinstance(timeout, int):
                hook["timeoutMs"] = timeout
            group: dict = {}
            matcher = spec.get("matcher")
            if isinstance(matcher, str) and matcher:
                group["matcher"] = matcher
            group["hooks"] = [hook]
            groups.append(group)
        built[event_name] = groups
    return built


def _strip_governance(events: object, hook_path: str) -> tuple[dict, int, dict]:
    """Return (events without governance groups, removed count, removed groups)."""
    removed = 0
    cleaned: dict = {}
    displaced: dict = {}
    if not isinstance(events, dict):
        return cleaned, removed, displaced
    for event_name, groups in events.items():
        if not isinstance(groups, list):
            cleaned[event_name] = groups
            continue
        kept = []
        dropped = []
        for group in groups:
            if _is_governance_entry(group, hook_path):
                removed += 1
                dropped.append(group)
                continue
            kept.append(group)
        if kept:
            cleaned[event_name] = kept
        if dropped:
            displaced[event_name] = dropped
    return cleaned, removed, displaced


def _atomic_write(path: pathlib.Path, text: str) -> None:
    mode = os.stat(path).st_mode & 0o777 if path.exists() else 0o600
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _sidecar_path(config_path: pathlib.Path, explicit_home: str | None) -> pathlib.Path:
    if explicit_home:
        home = pathlib.Path(explicit_home).expanduser()
    else:
        # config lives at <home>/cli/config.json, so <home> is its grandparent.
        home = config_path.resolve().parent.parent
    return home / SIDECAR_DIRNAME / SIDECAR_FILENAME


def _read_sidecar(path: pathlib.Path) -> dict | None:
    """Return the sidecar payload, or None when absent/unusable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SIDECAR_SCHEMA:
        return None
    if not isinstance(data.get("events"), dict):
        return None
    return data


def _sidecar_entry_count(payload: dict) -> int:
    total = 0
    for groups in payload["events"].values():
        if isinstance(groups, list):
            total += len(groups)
    return total


def _write_sidecar(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _snapshot_non_events(data: dict) -> dict[str, str]:
    """Canonical JSON of every key except hooks.events, for equality proof."""
    snap: dict[str, str] = {}
    for key, value in data.items():
        if key != "hooks":
            snap[key] = _canonical(value)
            continue
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_key == "events":
                    continue
                snap["hooks." + sub_key] = _canonical(sub_value)
        else:
            snap["hooks"] = _canonical(value)
    return snap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--zcode-home", default=None)
    ap.add_argument("--hook-path", default=None)
    ap.add_argument("--python", default=DEFAULT_PYTHON)
    ap.add_argument("--hooks-manifest", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--unregister", action="store_true")
    args = ap.parse_args(argv)

    config_path = (
        pathlib.Path(args.config).expanduser() if args.config else _default_config_path()
    )
    hook_path = (
        pathlib.Path(args.hook_path).expanduser() if args.hook_path else _default_hook_path()
    )
    hook_path_str = str(hook_path)

    data = _load_config(config_path)
    before_snapshot = _snapshot_non_events(data)
    original_text = config_path.read_text(encoding="utf-8")

    original_hooks = data.get("hooks")
    enabled_before = (
        original_hooks.get("enabled") if isinstance(original_hooks, dict) else None
    )

    sidecar_path = _sidecar_path(config_path, args.zcode_home)
    existing_sidecar = _read_sidecar(sidecar_path)

    updated = copy.deepcopy(data)
    hooks_block = updated.get("hooks")
    if not isinstance(hooks_block, dict):
        hooks_block = {}
        updated["hooks"] = hooks_block
    cleaned_events, removed, displaced_map = _strip_governance(
        hooks_block.get("events"), hook_path_str
    )

    added = 0
    displaced = 0
    restored = 0
    sidecar_payload: dict | None = None
    sidecar_report: object = None
    if args.unregister:
        if existing_sidecar is not None:
            for event_name, groups in existing_sidecar["events"].items():
                if not isinstance(groups, list) or not groups:
                    continue
                cleaned_events[event_name] = list(
                    cleaned_events.get(event_name, [])
                ) + list(groups)
                restored += len(groups)
            hooks_block["events"] = cleaned_events
            enabled_restore = existing_sidecar.get("hooks_enabled_before")
            if enabled_restore is None:
                hooks_block.pop("enabled", None)
            else:
                hooks_block["enabled"] = enabled_restore
            sidecar_report = str(sidecar_path)
        else:
            hooks_block["events"] = cleaned_events
        status = "UNREGISTERED"
    else:
        manifest_path = (
            pathlib.Path(args.hooks_manifest).expanduser()
            if args.hooks_manifest
            else hook_path.parent / "hooks.json"
        )
        events = _load_hook_manifest(manifest_path)
        built = _build_groups(events, hook_path_str, args.python)
        merged = dict(cleaned_events)
        for event_name, groups in built.items():
            merged[event_name] = list(groups) + list(merged.get(event_name, []))
            added += len(groups)
        hooks_block["events"] = merged
        hooks_block["enabled"] = True
        status = "REGISTERED"

        # Record what we displaced so --unregister can put it back.  Only skip
        # the sidecar when nothing was displaced *and* hooks were already on.
        if removed or enabled_before is not True:
            if existing_sidecar is not None:
                sidecar_report = "preserved"
            else:
                sidecar_payload = {
                    "schema": SIDECAR_SCHEMA,
                    "displaced_at": datetime.datetime.now(datetime.timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                    "config_sha256": hashlib.sha256(
                        original_text.encode("utf-8")
                    ).hexdigest(),
                    "hooks_enabled_before": enabled_before,
                    "events": displaced_map,
                }
                displaced = removed
                sidecar_report = str(sidecar_path)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "DRY_RUN",
                    "added": added,
                    "removed": removed,
                    "displaced": displaced,
                    "restored": restored,
                    "displaced_sidecar": sidecar_report,
                    "config_backup": None,
                    "untouched_top_level_keys": sorted(before_snapshot),
                },
                sort_keys=True,
            )
        )
        return 0

    new_text = json.dumps(updated, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    json.loads(new_text)  # pre-write parse check

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = config_path.with_name(config_path.name + BACKUP_PREFIX + stamp)
    shutil.copy2(config_path, backup)

    _atomic_write(config_path, new_text)

    try:
        reread = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(reread, dict):
            raise ValueError("root")
        after_snapshot = _snapshot_non_events(reread)
        # hooks.enabled is deliberately forced to true on register; every other
        # non-events key must be byte-identical in canonical form.
        expected = {k: v for k, v in before_snapshot.items() if k != "hooks.enabled"}
        actual = {k: v for k, v in after_snapshot.items() if k != "hooks.enabled"}
        if expected != actual:
            raise ValueError("non-events keys changed")
    except Exception:
        _atomic_write(config_path, original_text)
        print("register-hooks: verification failed, config rolled back", file=sys.stderr)
        return 1

    if sidecar_payload is not None:
        try:
            _write_sidecar(sidecar_path, sidecar_payload)
        except OSError:
            _atomic_write(config_path, original_text)
            print(
                "register-hooks: cannot write displaced-hooks sidecar, config rolled back",
                file=sys.stderr,
            )
            return 1
    elif args.unregister and existing_sidecar is not None:
        sidecar_path.unlink(missing_ok=True)

    print(
        json.dumps(
            {
                "status": status,
                "added": added,
                "removed": removed,
                "displaced": displaced,
                "restored": restored,
                "displaced_sidecar": sidecar_report,
                "config_backup": str(backup),
                "untouched_top_level_keys": sorted(before_snapshot),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
