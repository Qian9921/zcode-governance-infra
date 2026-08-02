"""Parameterizable role/agent model configuration for ZCode governance."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROLES_SCHEMA = "gov-roles.v1"
ROLE_NAMES = ("writer", "executor", "reviewer_standard", "reviewer_high", "auditor_spark")
EFFORT_NAMES = ("reviewer_standard", "reviewer_high", "delta_continuation", "auditor_spark")
VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})

_PLACEHOLDER_RE = re.compile(r"^<TBD(:[A-Za-z0-9_.-]+)?>$")
_AGENT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

DEFAULT_ROLES: dict[str, Any] = {
    "schema": "gov-roles.v1",
    "roles": {
        "writer": "tuzi-direct-1m/claude-tuzi/claude-opus-5",
        "executor": "e8ed5e30-e95d-45dc-b265-37acf2ba2583/deepseek-v4-flash",
        "reviewer_standard": "tuzi-direct-1m/claude-tuzi/claude-fable-5",
        "reviewer_high": "tuzi-direct-1m/claude-tuzi/claude-fable-5",
        "auditor_spark": "tuzi-direct-1m/claude-tuzi/claude-fable-5"
    },
    "efforts": {
        "reviewer_standard": "high",
        "reviewer_high": "xhigh",
        "delta_continuation": "high",
        "auditor_spark": "high"
    },
    "agents": {
        "reviewer_standard": "gov-reviewer",
        "reviewer_high": "gov-reviewer",
        "executor": "gov-executor",
        "auditor_spark": "gov-spark-audit"
    }
}


class RoleError(ValueError):
    """Configuration or validation error for governance roles."""


class RolePlaceholderUnresolved(RoleError):
    """A role still contains a placeholder and cannot be resolved."""


def is_placeholder(value: Any) -> bool:
    """Return True if *value* is a '<TBD>' or '<TBD:suffix>' placeholder."""
    return isinstance(value, str) and bool(_PLACEHOLDER_RE.fullmatch(value))


def default_roles_path() -> Path:
    """Return the default roles configuration path."""
    if "ZCODE_HOME" in os.environ:
        return Path(os.environ["ZCODE_HOME"]) / "gov-config" / "roles.json"
    return Path.home() / ".zcode" / "gov-config" / "roles.json"


def _resolve_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("ZGOV_ROLES_PATH")
    if env_path:
        return Path(env_path)
    return default_roles_path()


def _deepcopy(value: Any) -> Any:
    """Produce a deep copy using only stdlib primitives."""
    if isinstance(value, dict):
        return {k: _deepcopy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deepcopy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_deepcopy(v) for v in value)
    return value


def load_roles(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load and validate the roles configuration.

    If *path* is not provided, resolve it via ``ZGOV_ROLES_PATH``,
    ``$ZCODE_HOME/gov-config/roles.json``, then ``~/.zcode/gov-config/roles.json``.

    If the resolved file does not exist, return the built-in placeholder defaults.
    JSON or structural errors raise ``RoleError``.
    """
    target = _resolve_path(path)
    if not target.exists():
        return _deepcopy(DEFAULT_ROLES)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RoleError(f"{target}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise RoleError(f"{target}: cannot read: {exc}") from exc
    return validate_roles(raw, path=str(target))


def validate_roles(value: Any, path: str = "$") -> dict[str, Any]:
    """Strictly validate a roles configuration object.

    Raises ``RoleError`` with ``{path}.{field}: <reason>`` messages.
    """
    if not isinstance(value, dict):
        raise RoleError(f"{path}: object required")

    allowed_top = {"schema", "roles", "efforts", "agents"}
    extra = set(value) - allowed_top
    if extra:
        raise RoleError(f"{path}.{next(iter(sorted(extra)))}: additional property not allowed")

    if value.get("schema") != ROLES_SCHEMA:
        raise RoleError(f"{path}.schema: must be '{ROLES_SCHEMA}'")

    roles = value.get("roles")
    if not isinstance(roles, dict):
        raise RoleError(f"{path}.roles: object required")
    if set(roles) != set(ROLE_NAMES):
        missing = sorted(set(ROLE_NAMES) - set(roles))
        extra = sorted(set(roles) - set(ROLE_NAMES))
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"extra {extra}")
        raise RoleError(f"{path}.roles: keys must equal {ROLE_NAMES} ({'; '.join(parts)})")
    for name in ROLE_NAMES:
        role_value = roles[name]
        if not isinstance(role_value, str) or not role_value:
            raise RoleError(f"{path}.roles.{name}: non-empty string required")

    efforts = value.get("efforts")
    if not isinstance(efforts, dict):
        raise RoleError(f"{path}.efforts: object required")
    if set(efforts) != set(EFFORT_NAMES):
        missing = sorted(set(EFFORT_NAMES) - set(efforts))
        extra = sorted(set(efforts) - set(EFFORT_NAMES))
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"extra {extra}")
        raise RoleError(f"{path}.efforts: keys must equal {EFFORT_NAMES} ({'; '.join(parts)})")
    for name in EFFORT_NAMES:
        effort_value = efforts[name]
        if is_placeholder(effort_value):
            raise RoleError(f"{path}.efforts.{name}: effort values cannot be placeholders")
        if effort_value not in VALID_EFFORTS:
            raise RoleError(
                f"{path}.efforts.{name}: must be one of {sorted(VALID_EFFORTS)}"
            )
    if efforts["delta_continuation"] != efforts["reviewer_standard"]:
        raise RoleError(
            f"{path}.efforts.delta_continuation: must equal efforts.reviewer_standard "
            "(a low/medium-risk delta continuation must satisfy both effort rules)"
        )

    agents = value.get("agents")
    if agents is None:
        agents = {}
    if not isinstance(agents, dict):
        raise RoleError(f"{path}.agents: object required")
    for key, agent_value in agents.items():
        if not isinstance(key, str) or not _AGENT_KEY_RE.fullmatch(key):
            raise RoleError(f"{path}.agents: invalid key '{key}'")
        if not isinstance(agent_value, str) or not agent_value:
            raise RoleError(f"{path}.agents.{key}: non-empty string required")

    return {
        "schema": value["schema"],
        "roles": {name: roles[name] for name in ROLE_NAMES},
        "efforts": {name: efforts[name] for name in EFFORT_NAMES},
        "agents": dict(agents),
    }


def _ensure_roles(roles: dict[str, Any] | None) -> dict[str, Any]:
    return load_roles() if roles is None else roles


def resolve_role(name: str, roles: dict[str, Any] | None = None) -> str:
    """Return the resolved model identifier for *name*.

    Raises ``RoleError`` if *name* is unknown and ``RolePlaceholderUnresolved``
    if the value is still a placeholder.
    """
    if name not in ROLE_NAMES:
        raise RoleError(f"unknown role '{name}'")
    cfg = _ensure_roles(roles)
    value = cfg["roles"][name]
    if is_placeholder(value):
        raise RolePlaceholderUnresolved(
            f"ROLE_PLACEHOLDER_UNRESOLVED: role '{name}' is unresolved ({value})"
        )
    return value


def role_or_placeholder(name: str, roles: dict[str, Any] | None = None) -> str:
    """Return the raw role value, placeholder or not."""
    if name not in ROLE_NAMES:
        raise RoleError(f"unknown role '{name}'")
    cfg = _ensure_roles(roles)
    return cfg["roles"][name]


def roles_resolved(roles: dict[str, Any] | None = None) -> bool:
    """Return True when every known role has a non-placeholder value."""
    cfg = _ensure_roles(roles)
    return all(not is_placeholder(cfg["roles"][name]) for name in ROLE_NAMES)


def require_roles_resolved(roles: dict[str, Any] | None = None) -> None:
    """Raise ``RolePlaceholderUnresolved`` unless all roles are resolved."""
    cfg = _ensure_roles(roles)
    unresolved = sorted(
        name for name in ROLE_NAMES if is_placeholder(cfg["roles"][name])
    )
    if unresolved:
        raise RolePlaceholderUnresolved(
            f"ROLE_PLACEHOLDER_UNRESOLVED: unresolved roles {unresolved}"
        )


def effort_for(name: str, roles: dict[str, Any] | None = None) -> str:
    """Return the configured reasoning effort for *name*."""
    if name not in EFFORT_NAMES:
        raise RoleError(f"unknown effort '{name}'")
    cfg = _ensure_roles(roles)
    return cfg["efforts"][name]


def agent_for(name: str, roles: dict[str, Any] | None = None) -> str | None:
    """Return the configured agent for *name*, or None if missing/placeholder."""
    cfg = _ensure_roles(roles)
    agents = cfg.get("agents", {})
    value = agents.get(name)
    if value is None or is_placeholder(value):
        return None
    return value
