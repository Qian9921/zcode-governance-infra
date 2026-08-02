"""Parameterizable milestone freeze configuration for ZCode governance."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

MILESTONE_SCHEMA = "gov-milestone.v1"
MAX_SPARK_AUDITS = 3

_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_FINDING_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]{1,127}$")
_VALID_STAGES = frozenset({"targeted", "full", "fresh"})


class MilestoneError(ValueError):
    """Configuration or validation error for a governance milestone."""


class MilestoneNotFrozen(MilestoneError):
    """The milestone has not been frozen yet."""


def default_milestone_path() -> Path:
    """Return the default milestone configuration path."""
    if "ZCODE_HOME" in os.environ:
        return Path(os.environ["ZCODE_HOME"]) / "gov-config" / "milestone.json"
    return Path.home() / ".zcode" / "gov-config" / "milestone.json"


def _resolve_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path)
    env_path = os.environ.get("ZGOV_MILESTONE_PATH")
    if env_path:
        return Path(env_path)
    return default_milestone_path()


def _deepcopy(value: Any) -> Any:
    """Produce a deep copy using only stdlib primitives."""
    if isinstance(value, dict):
        return {k: _deepcopy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deepcopy(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_deepcopy(v) for v in value)
    return value


TRANSCRIPT_FIELDS = (
    "mission_scope_sha256",
    "compiled_plan_sha256",
    "audited_parent_sha",
    "author_closure_plan_sha256",
)


def _default_transcript() -> dict[str, Any]:
    return {name: None for name in TRANSCRIPT_FIELDS}


def _default_milestone() -> dict[str, Any]:
    return {
        "schema": MILESTONE_SCHEMA,
        "frozen": False,
        "milestone_id": None,
        "repo": None,
        "base_sha": None,
        "base_tree": None,
        "spark": {
            "audit_ids": [],
            "expected_raw_platform_sha256": {},
            "normalized_finding_ids": {},
            "author_closure_denominator": None,
            "author_finding_ids": [],
            "historical_findings": {},
            "expected_historical": {},
            "expected_current": {},
        },
        "transcript": _default_transcript(),
        "gate_stage_map": {"G-TARGETED": "targeted", "G-FULL": "full", "G-FRESH": "fresh"},
    }


def load_milestone(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load and validate the milestone configuration.

    If *path* is not provided, resolve it via ``ZGOV_MILESTONE_PATH``,
    ``$ZCODE_HOME/gov-config/milestone.json``, then ``~/.zcode/gov-config/milestone.json``.

    If the resolved file does not exist, return the built-in un-frozen defaults.
    JSON or structural errors raise ``MilestoneError``.
    """
    target = _resolve_path(path)
    if not target.exists():
        return _default_milestone()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MilestoneError(f"{target}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise MilestoneError(f"{target}: cannot read: {exc}") from exc
    return validate_milestone(raw, path=str(target))


def _validate_sha40(value: Any, path: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not _HEX40_RE.fullmatch(value):
        raise MilestoneError(f"{path}: must be null or 40-character lowercase hex")


def _validate_sha64(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise MilestoneError(f"{path}: must be 64-character lowercase hex")


def _validate_optional_sha64(value: Any, path: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        raise MilestoneError(f"{path}: must be null or 64-character lowercase hex")


def validate_milestone(value: Any, path: str = "$") -> dict[str, Any]:
    """Strictly validate a milestone configuration object.

    Raises ``MilestoneError`` with ``{path}.{field}: <reason>`` messages.
    """
    if not isinstance(value, dict):
        raise MilestoneError(f"{path}: object required")

    allowed_top = {
        "schema",
        "frozen",
        "milestone_id",
        "repo",
        "base_sha",
        "base_tree",
        "spark",
        "transcript",
        "gate_stage_map",
    }
    extra = set(value) - allowed_top
    if extra:
        raise MilestoneError(f"{path}.{next(iter(sorted(extra)))}: additional property not allowed")

    if value.get("schema") != MILESTONE_SCHEMA:
        raise MilestoneError(f"{path}.schema: must be '{MILESTONE_SCHEMA}'")

    frozen = value.get("frozen")
    if type(frozen) is not bool:
        raise MilestoneError(f"{path}.frozen: strict boolean required")

    milestone_id = value.get("milestone_id")
    repo = value.get("repo")
    base_sha = value.get("base_sha")
    base_tree = value.get("base_tree")

    _validate_sha40(base_sha, f"{path}.base_sha")
    _validate_sha40(base_tree, f"{path}.base_tree")

    spark = value.get("spark")
    if not isinstance(spark, dict):
        raise MilestoneError(f"{path}.spark: object required")

    allowed_spark = {
        "audit_ids",
        "expected_raw_platform_sha256",
        "normalized_finding_ids",
        "author_closure_denominator",
        "author_finding_ids",
        "historical_findings",
        "expected_historical",
        "expected_current",
    }
    extra_spark = set(spark) - allowed_spark
    if extra_spark:
        raise MilestoneError(
            f"{path}.spark.{next(iter(sorted(extra_spark)))}: additional property not allowed"
        )

    audit_ids = spark.get("audit_ids")
    if not isinstance(audit_ids, list):
        raise MilestoneError(f"{path}.spark.audit_ids: list required")
    if len(audit_ids) > MAX_SPARK_AUDITS:
        raise MilestoneError(
            f"{path}.spark.audit_ids: length must be <= {MAX_SPARK_AUDITS}"
        )
    seen_audits: set[str] = set()
    for index, aid in enumerate(audit_ids):
        if not isinstance(aid, str) or not aid:
            raise MilestoneError(f"{path}.spark.audit_ids[{index}]: non-empty string required")
        if aid in seen_audits:
            raise MilestoneError(f"{path}.spark.audit_ids[{index}]: duplicate audit id '{aid}'")
        seen_audits.add(aid)

    raw_hashes = spark.get("expected_raw_platform_sha256")
    if not isinstance(raw_hashes, dict):
        raise MilestoneError(f"{path}.spark.expected_raw_platform_sha256: object required")
    for key, raw_hash in raw_hashes.items():
        if not isinstance(key, str):
            raise MilestoneError(f"{path}.spark.expected_raw_platform_sha256: keys must be strings")
        _validate_sha64(raw_hash, f"{path}.spark.expected_raw_platform_sha256.{key}")

    normalized = spark.get("normalized_finding_ids")
    if not isinstance(normalized, dict):
        raise MilestoneError(f"{path}.spark.normalized_finding_ids: object required")
    for key, finding_list in normalized.items():
        if not isinstance(key, str):
            raise MilestoneError(f"{path}.spark.normalized_finding_ids: keys must be strings")
        if not isinstance(finding_list, list):
            raise MilestoneError(f"{path}.spark.normalized_finding_ids.{key}: list required")
        seen_ids: set[str] = set()
        for index, fid in enumerate(finding_list):
            loc = f"{path}.spark.normalized_finding_ids.{key}[{index}]"
            if not isinstance(fid, str) or not _FINDING_ID_RE.fullmatch(fid):
                raise MilestoneError(
                    f"{loc}: must match '^[A-Za-z][A-Za-z0-9_.:/-]{{1,127}}$'"
                )
            if fid in seen_ids:
                raise MilestoneError(f"{loc}: duplicate finding id '{fid}'")
            seen_ids.add(fid)

    denominator = spark.get("author_closure_denominator")
    if denominator is not None:
        if type(denominator) is not int or isinstance(denominator, bool) or denominator <= 0:
            raise MilestoneError(
                f"{path}.spark.author_closure_denominator: must be null or positive integer"
            )

    author_finding_ids = spark.get("author_finding_ids")
    if author_finding_ids is None:
        author_finding_ids = []
    if not isinstance(author_finding_ids, list):
        raise MilestoneError(f"{path}.spark.author_finding_ids: list required")
    for index, fid in enumerate(author_finding_ids):
        if not isinstance(fid, str):
            raise MilestoneError(
                f"{path}.spark.author_finding_ids[{index}]: string required"
            )

    historical_findings = spark.get("historical_findings")
    if historical_findings is None:
        historical_findings = {}
    if not isinstance(historical_findings, dict):
        raise MilestoneError(f"{path}.spark.historical_findings: object required")

    # ``transcript`` carries the dispatch-transcript identity pins.  It is
    # optional for backward compatibility; when absent every pin is null and
    # the dependent validators fall back to shape-only checks.
    if "transcript" in value:
        transcript = value["transcript"]
        if not isinstance(transcript, dict):
            raise MilestoneError(f"{path}.transcript: object required")
        extra_transcript = set(transcript) - set(TRANSCRIPT_FIELDS)
        if extra_transcript:
            raise MilestoneError(
                f"{path}.transcript.{next(iter(sorted(extra_transcript)))}: "
                "additional property not allowed"
            )
        missing_transcript = set(TRANSCRIPT_FIELDS) - set(transcript)
        if missing_transcript:
            raise MilestoneError(
                f"{path}.transcript.{next(iter(sorted(missing_transcript)))}: required property"
            )
    else:
        transcript = _default_transcript()

    _validate_optional_sha64(
        transcript["mission_scope_sha256"], f"{path}.transcript.mission_scope_sha256"
    )
    _validate_optional_sha64(
        transcript["compiled_plan_sha256"], f"{path}.transcript.compiled_plan_sha256"
    )
    _validate_sha40(transcript["audited_parent_sha"], f"{path}.transcript.audited_parent_sha")
    _validate_optional_sha64(
        transcript["author_closure_plan_sha256"],
        f"{path}.transcript.author_closure_plan_sha256",
    )

    gate_stage_map = value.get("gate_stage_map")
    if not isinstance(gate_stage_map, dict):
        raise MilestoneError(f"{path}.gate_stage_map: object required")
    seen_stages: set[str] = set()
    for key, stage in gate_stage_map.items():
        if not isinstance(key, str):
            raise MilestoneError(f"{path}.gate_stage_map: keys must be strings")
        if not isinstance(stage, str) or stage not in _VALID_STAGES:
            raise MilestoneError(
                f"{path}.gate_stage_map.{key}: must be one of {sorted(_VALID_STAGES)}"
            )
        if stage in seen_stages:
            raise MilestoneError(
                f"{path}.gate_stage_map.{key}: stage '{stage}' already mapped"
            )
        seen_stages.add(stage)

    if frozen:
        if not isinstance(milestone_id, str) or not milestone_id:
            raise MilestoneError(f"{path}.milestone_id: required when frozen")
        if repo is None or (isinstance(repo, str) and not repo):
            raise MilestoneError(f"{path}.repo: required when frozen")
        if base_sha is None or base_tree is None:
            raise MilestoneError(
                f"{path}.base_sha: base_sha and base_tree are required when frozen"
            )
        raw_keys = set(raw_hashes)
        if set(audit_ids) != raw_keys:
            raise MilestoneError(
                f"{path}.spark.audit_ids: must equal expected_raw_platform_sha256 keys when frozen"
            )
        if not set(normalized).issubset(set(audit_ids)):
            raise MilestoneError(
                f"{path}.spark.normalized_finding_ids: keys must be a subset of audit_ids when frozen"
            )
        if denominator is not None and denominator != len(author_finding_ids):
            raise MilestoneError(
                f"{path}.spark.author_closure_denominator: must equal len(author_finding_ids) when frozen"
            )

    return {
        "schema": value["schema"],
        "frozen": frozen,
        "milestone_id": milestone_id,
        "repo": repo,
        "base_sha": base_sha,
        "base_tree": base_tree,
        "spark": {
            "audit_ids": list(audit_ids),
            "expected_raw_platform_sha256": dict(raw_hashes),
            "normalized_finding_ids": {
                k: list(v) for k, v in normalized.items()
            },
            "author_closure_denominator": denominator,
            "author_finding_ids": list(author_finding_ids),
            "historical_findings": dict(historical_findings),
            "expected_historical": _deepcopy(spark.get("expected_historical")),
            "expected_current": _deepcopy(spark.get("expected_current")),
        },
        "transcript": {name: transcript[name] for name in TRANSCRIPT_FIELDS},
        "gate_stage_map": dict(gate_stage_map),
    }


def _ensure_milestone(ms: dict[str, Any] | None) -> dict[str, Any]:
    return load_milestone() if ms is None else ms


def is_frozen(ms: dict[str, Any] | None = None) -> bool:
    """Return True if the milestone is frozen."""
    cfg = _ensure_milestone(ms)
    return bool(cfg["frozen"])


def require_frozen(ms: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the milestone config or raise ``MilestoneNotFrozen``."""
    cfg = _ensure_milestone(ms)
    if not cfg["frozen"]:
        raise MilestoneNotFrozen(
            "MILESTONE_NOT_FROZEN: milestone has not been frozen"
        )
    return cfg


def base_identity(ms: dict[str, Any] | None = None) -> tuple[str | None, str | None]:
    """Return (base_sha, base_tree)."""
    cfg = _ensure_milestone(ms)
    return (cfg["base_sha"], cfg["base_tree"])


def spark_config(ms: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the spark configuration subsection."""
    cfg = _ensure_milestone(ms)
    return cfg["spark"]


def transcript_config(ms: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the dispatch-transcript identity pins.

    Missing pins are reported as ``None``; callers must treat a ``None`` pin as
    "shape-only verification" rather than as a satisfied identity check.
    """
    cfg = _ensure_milestone(ms)
    transcript = cfg.get("transcript") or {}
    return {name: transcript.get(name) for name in TRANSCRIPT_FIELDS}


def gate_stage_map(ms: dict[str, Any] | None = None) -> dict[str, str]:
    """Return the gate-to-stage mapping."""
    cfg = _ensure_milestone(ms)
    return cfg["gate_stage_map"]
