#!/usr/bin/env python3
"""Deterministic pre-tool decision and non-invasive routing signal (ZCode port).

The normalized tool name owns policy.  Arguments have zero effect on allow/deny;
for a generic execution tool (``Bash``) only a direct first executable of
``rtk`` or ``rg`` is classified, and only to produce a privacy-safe routing
receipt.  Raw arguments are never persisted and intent is never inferred from
them.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import sys
from collections.abc import Mapping
from typing import Any

try:  # Support both direct hook execution and package-based test discovery.
    from . import hook_receipt
except ImportError:  # pragma: no cover - exercised by direct script invocation.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import hook_receipt

# ``zgov`` is optional: hooks must keep working when the package is absent.
try:
    sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
    from zgov.tool_routing import normalize_zcode_tool as _normalize_zcode_tool
    ROUTING_MODULE_AVAILABLE = True
except Exception:  # pragma: no cover - exercised by the stdlib-only test
    _normalize_zcode_tool = None
    ROUTING_MODULE_AVAILABLE = False


# Retained from the Codex line for backward compatibility.  Under ZCode a tool
# name is ``Bash``/``Agent``/``Read``/``mcp__*``; it is never ``git`` or
# ``merge``, so this set is effectively inert here.  The real GitHub/git
# governance lives in ``zcode_hook.py`` (``identity_guard`` and ``merge_gate``),
# which gate the *command* under the ``Bash`` tool.  The mechanism is kept so a
# host that does expose such explicit child-action tool names still denies them.
FORBIDDEN_CHILD = frozenset({"git", "github", "merge", "review", "approve"})

# Same semantics as ``zgov.tool_routing.ZCODE_TOOL_ALIASES``, kept inline so the
# hook can route without the ``zgov`` package installed.
ROUTE_BY_TOOL = {
    "mcp__codegraph__codegraph_explore": "codegraph",
    "codegraph": "codegraph",
    "codegraph_explore": "codegraph",
    "mcp__semble__search": "semble",
    "mcp__semble__find_related": "semble",
    "semble": "semble",
    "semble_search": "semble",
    "rtk": "rtk",
    "grep": "rg",
    "rg": "rg",
    "ripgrep": "rg",
}
_GENERIC_EXECUTION = frozenset({"bash", "shell", "exec_command", "functions.exec_command"})
_COMPOUND_CHARS = "|&;><`$(\n"


def routing_available() -> bool:
    """Whether the authoritative ``zgov`` router backed this module's routing."""

    return ROUTING_MODULE_AVAILABLE


def _tool_key(tool: Any) -> str:
    """Normalize only a tool label; never stringify or inspect arguments."""

    return tool.strip().lower() if isinstance(tool, str) else ""


def _direct_shell_route(tool: Any, args: Any) -> str | None:
    if _tool_key(tool) not in _GENERIC_EXECUTION or not isinstance(args, Mapping):
        return None
    command = args.get("command", args.get("cmd"))
    if not isinstance(command, str) or not command.strip():
        return None
    if any(char in command for char in _COMPOUND_CHARS):
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens:
        return None
    executable = pathlib.PurePosixPath(tokens[0]).name.lower()
    return executable if executable in {"rtk", "rg"} else None


def _fallback_route(tool: Any, args: Any) -> str | None:
    key = _tool_key(tool)
    if key in ROUTE_BY_TOOL:
        return ROUTE_BY_TOOL[key]
    if key.startswith("mcp__codegraph"):
        return "codegraph"
    if key.startswith("mcp__semble"):
        return "semble"
    return _direct_shell_route(tool, args)


def route_for(tool: Any, args: Any = None) -> str:
    """Return an explicit route hint, or ``unspecified`` when not known."""

    if ROUTING_MODULE_AVAILABLE:
        try:
            resolved = _normalize_zcode_tool(tool, args if isinstance(args, Mapping) else None)
        except Exception:
            resolved = None
        if resolved:
            return resolved
        return "unspecified"
    return _fallback_route(tool, args) or "unspecified"


def decide(tool: Any, args: Any = None) -> dict[str, str]:
    """Return a stable allow/deny result.

    ``args`` has no policy effect.  It may supply only the direct executable
    route hint described by :func:`_direct_shell_route`.  Parent authorization
    remains required for the explicit child-action tools in
    :data:`FORBIDDEN_CHILD`.
    """

    key = _tool_key(tool)
    route = route_for(tool, args)
    if key in FORBIDDEN_CHILD:
        return {
            "decision": "deny",
            "reason": "child action requires parent authorization",
            "reason_code": "child_action_requires_parent_authorization",
            "route": route,
            "route_code": route.lower(),
        }
    return {
        "decision": "allow",
        "reason": "policy-pass",
        "reason_code": "policy_pass",
        "route": route,
        "route_code": route.lower(),
    }


if __name__ == "__main__":
    try:
        x = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        x = {}
    if not isinstance(x, dict):
        x = {}
    tool_name = x.get("tool_name", x.get("tool", ""))
    tool_input = x.get("tool_input", x.get("args"))
    result = decide(tool_name, tool_input)
    receipt_value = hook_receipt.receipt(
        "PreToolUse",
        x.get("model", os.environ.get("ZCODE_MODEL", "unknown")),
        tool=tool_name,
        decision=result["decision"],
        reason_code=result["reason_code"],
        route_code=result["route_code"],
        identifiers=x,
    )
    result["receipt_status"] = "success" if hook_receipt.write_receipt(receipt_value) else "write_failed"
    print(json.dumps(result, sort_keys=True))
