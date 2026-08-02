"""Bounded Spark inner-loop audit packet/result protocol."""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from . import roles
from .contracts import (
    ContractError,
    _id,
    _int,
    _relative_path,
    _sha,
    _str,
    canonical_json,
    canonical_sha256,
    counterexample_sha256,
    validate_closure_binding_receipt,
)
from .milestone import MAX_SPARK_AUDITS, is_frozen, require_frozen
from . import milestone

HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
_PRIVATE_ARTIFACT_RE = re.compile(r"(?:gh[pso]_[A-Za-z0-9]{12,}|/" + r"home/|/" + r"Users/|\x00)", re.I)


def _spark_model(roles_cfg: Mapping[str, Any] | None = None) -> str:
    """Return the configured Spark auditor model or its placeholder."""
    return roles.role_or_placeholder("auditor_spark", roles_cfg)


def _spark_model_matches(value: Any, roles_cfg: Mapping[str, Any] | None = None) -> bool:
    """True if *value* matches the configured Spark model or the role is unresolved."""
    expected = _spark_model(roles_cfg)
    return roles.is_placeholder(expected) or value == expected


def expected_raw_platform_sha256(ms: Mapping[str, Any] | None = None) -> Mapping[str, str]:
    """Lazy accessor for the frozen raw-platform SHA-256 facts."""
    return milestone.spark_config(ms)["expected_raw_platform_sha256"]


def normalized_finding_ids(ms: Mapping[str, Any] | None = None) -> Mapping[str, Sequence[str]]:
    """Lazy accessor for the frozen normalized finding ID sets."""
    return milestone.spark_config(ms)["normalized_finding_ids"]


def historical_findings(ms: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """Lazy accessor for the frozen historical finding disposition map."""
    return milestone.spark_config(ms)["historical_findings"]


def expected_historical(ms: Mapping[str, Any] | None = None) -> Sequence[Any]:
    """Lazy accessor for the frozen historical audit task identities."""
    return milestone.spark_config(ms)["expected_historical"]


def expected_current(ms: Mapping[str, Any] | None = None) -> Sequence[Mapping[str, Any]]:
    """Lazy accessor for the frozen accepted-current audit records."""
    return milestone.spark_config(ms)["expected_current"]


def author_finding_ids(ms: Mapping[str, Any] | None = None) -> Sequence[str]:
    """Lazy accessor for the frozen author-closure finding ID set."""
    return tuple(milestone.spark_config(ms)["author_finding_ids"])


def author_closure_denominator(ms: Mapping[str, Any] | None = None) -> int | None:
    """Lazy accessor for the frozen author-closure denominator.

    If the milestone is frozen and the denominator is unset, fall back to the
    length of the author finding ID list.
    """
    cfg = milestone.spark_config(ms)
    denom = cfg["author_closure_denominator"]
    if denom is None and is_frozen(ms):
        return len(cfg["author_finding_ids"])
    return denom


class SparkAuditError(ContractError):
    pass


def _hash64(value: Any, path: str) -> str:
    value = _str(value, path, max_len=64, public=True)
    if not HEX64_RE.fullmatch(value):
        raise SparkAuditError("SHA-256 artifact identity required", path)
    return value


def _validate_normalized_result(
    value: Any,
    *,
    audit_id: str,
    expected_raw_sha: str,
    milestone_cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("normalized Spark result object required")
    fields = {"schema", "audit_id", "task_id", "scope", "raw_platform_sha256", "status", "finding_count", "findings", "dispositions", "artifact_sha256"}
    if set(value) != fields:
        raise SparkAuditError("strict normalized result fields")
    if value["schema"] != "spark-normalized-result.v16" or value["audit_id"] != audit_id or value["status"] != "AUDIT_COMPLETE":
        raise SparkAuditError("normalized result identity/status mismatch")
    _str(value["task_id"], "$.task_id", max_len=512, public=True)
    if not isinstance(value["scope"], list) or not value["scope"] or any(not isinstance(s, str) or not s for s in value["scope"]):
        raise SparkAuditError("normalized result scope required")
    if value["raw_platform_sha256"] != expected_raw_sha:
        raise SparkAuditError("raw platform result hash mismatch")
    _hash64(value["raw_platform_sha256"], "$.raw_platform_sha256")
    ids = list(normalized_finding_ids(milestone_cfg).get(audit_id, []))
    findings = value["findings"]
    if not isinstance(findings, list) or value["finding_count"] != len(ids) or len(findings) != len(ids):
        raise SparkAuditError("normalized finding denominator mismatch")
    seen: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise SparkAuditError("normalized finding object required", f"$.findings[{index}]")
        if finding.get("id") not in ids or finding["id"] in seen:
            raise SparkAuditError("normalized finding identity mismatch", f"$.findings[{index}].id")
        seen.add(finding["id"])
        # A/B normalizations carry explicit raw ``status``/``disposition``
        # fields; C's source packet records those facts in the top-level
        # dispositions map.  Enforce the fields when present, while requiring
        # the map below for every finding so neither representation can drift.
        if finding.get("severity") != "P1" or finding.get("label") != "BLOCKING":
            raise SparkAuditError("raw corrective finding/FOLLOW_UP fact changed", f"$.findings[{index}]")
        if "status" in finding and finding["status"] != "OPEN":
            raise SparkAuditError("raw corrective finding status changed", f"$.findings[{index}]")
        if "disposition" in finding and finding["disposition"] != "FOLLOW_UP":
            raise SparkAuditError("raw corrective finding disposition changed", f"$.findings[{index}]")
        if _PRIVATE_ARTIFACT_RE.search(canonical_json(finding)):
            raise SparkAuditError("privacy-sensitive normalized finding", f"$.findings[{index}]")
    if not isinstance(value["dispositions"], dict) or set(value["dispositions"]) != set(ids) or any(v != "FOLLOW_UP" for v in value["dispositions"].values()):
        raise SparkAuditError("raw per-finding FOLLOW_UP dispositions required")
    unsigned = dict(value); unsigned["artifact_sha256"] = ""
    if value["artifact_sha256"] != canonical_sha256(unsigned):
        raise SparkAuditError("normalized result digest mismatch")
    return dict(value)


def validate_author_closure_plan(
    value: Any,
    *,
    root: str | pathlib.Path | None = None,
    milestone_cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Trust-chain validators require a frozen milestone baseline.  This is
    # checked first so no structural error can mask the missing trust root.
    cfg = require_frozen(milestone_cfg)
    stage_for_gate = milestone.gate_stage_map(cfg)
    if not isinstance(value, dict):
        raise SparkAuditError("author closure plan object required")
    fields = {"schema", "mission_id", "audited_head_sha", "finding_count", "findings", "candidate_binding", "plan_sha256"}
    if set(value) != fields or value.get("schema") != "author-closure-plan.v16" or value.get("mission_id") != "V16-PRODUCTIVITY" or value.get("candidate_binding") != "external_evidence_envelope":
        raise SparkAuditError("strict author closure plan fields")
    # Git object identities are 40-hex SHAs, not SHA-256 artifact digests.
    # The audited parent is pinned by the transcript's own snapshot equality and
    # the base->audited->candidate ancestry chain, not by a literal in this file.
    _sha(value["audited_head_sha"], "$.audited_head_sha")
    denom = author_closure_denominator(cfg)
    if denom is None:
        raise SparkAuditError("author closure denominator unavailable")
    author_ids = author_finding_ids(cfg)
    if value["finding_count"] != denom or not isinstance(value["findings"], list) or len(value["findings"]) != denom:
        raise SparkAuditError("closure plan denominator must be 18")
    required = {"finding_id", "severity", "counterexample_id", "executable_counterexample_id", "source_location", "gate_id", "stage", "evidence_row_id", "acceptance"}
    seen: set[str] = set()
    for index, item in enumerate(value["findings"]):
        if not isinstance(item, dict) or set(item) != required:
            raise SparkAuditError("strict closure plan finding fields", f"$.findings[{index}]")
        fid = item["finding_id"]
        if fid not in author_ids or fid in seen:
            raise SparkAuditError("closure plan finding identity mismatch", f"$.findings[{index}].finding_id")
        seen.add(fid)
        if item["severity"] != "P1" or item["counterexample_id"] not in {"CE-SCHEMA", "CE-GATE", "CE-EVIDENCE"} or not re.fullmatch(r"NF-[0-9]{3}", item["executable_counterexample_id"]):
            raise SparkAuditError("closure plan executable counterexample binding required", f"$.findings[{index}]")
        location = item["source_location"]
        if not isinstance(location, str) or ":" not in location or location.startswith(("/", "~")) or _PRIVATE_ARTIFACT_RE.search(location):
            raise SparkAuditError("portable closure plan source location required", f"$.findings[{index}].source_location")
        if item["stage"] not in {"targeted", "full", "fresh"} or item["gate_id"] not in stage_for_gate or item["stage"] != stage_for_gate[item["gate_id"]]:
            raise SparkAuditError("closure plan gate/stage mismatch", f"$.findings[{index}]")
        if item["evidence_row_id"] != f"EVID-{item['gate_id']}-EP-UNIT" or not isinstance(item["acceptance"], str) or not item["acceptance"]:
            raise SparkAuditError("closure plan evidence/acceptance binding required", f"$.findings[{index}]")
    if seen != set(author_ids):
        raise SparkAuditError("closure plan must cover all 18 findings")
    unsigned = dict(value); unsigned["plan_sha256"] = ""
    if value["plan_sha256"] != canonical_sha256(unsigned):
        raise SparkAuditError("closure plan digest mismatch")
    if root is not None:
        root_path = pathlib.Path(root).resolve()
        for item in value["findings"]:
            path = (root_path / item["source_location"].split(":", 1)[0]).resolve()
            try:
                path.relative_to(root_path)
            except ValueError:
                raise SparkAuditError("closure plan source escapes candidate root")
            if not path.is_file() or path.is_symlink():
                raise SparkAuditError("closure plan source location missing")
    return dict(value)


def build_closure_binding_receipt(
    closure_plan: Mapping[str, Any],
    *,
    compiled_plan: Mapping[str, Any],
    spark_requests: Sequence[Mapping[str, Any]],
    spark_results: Sequence[Mapping[str, Any]],
    closure_plan_file_sha256: str,
    dispatch_transcript_file_sha256: str,
    normalized_source_artifact_paths: Mapping[str, str],
    root: str | pathlib.Path | None = None,
    milestone_cfg: Mapping[str, Any] | None = None,
    roles_cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze finding-to-execution bindings before any closure-aware gate runs."""
    cfg = require_frozen(milestone_cfg)
    plan = validate_author_closure_plan(dict(closure_plan), root=root, milestone_cfg=cfg)
    if not isinstance(compiled_plan, Mapping) or compiled_plan.get("schema") != "compiled-plan.v16":
        raise SparkAuditError("compiled mission plan required for closure binding receipt")
    if compiled_plan.get("mission_id") != plan["mission_id"]:
        raise SparkAuditError("closure/compiled mission mismatch")
    compiled_plan_sha256 = canonical_sha256(compiled_plan)
    for value, path in (
        (closure_plan_file_sha256, "$.closure_plan_file_sha256"),
        (dispatch_transcript_file_sha256, "$.dispatch_transcript_file_sha256"),
    ):
        _hash64(value, path)

    gates = {
        gate.get("id"): gate
        for gate in compiled_plan.get("gates", [])
        if isinstance(gate, Mapping) and isinstance(gate.get("id"), str)
    }
    entries = {
        entry.get("id"): entry
        for entry in compiled_plan.get("entrypoints", [])
        if isinstance(entry, Mapping) and isinstance(entry.get("id"), str)
    }
    source_findings: dict[str, Mapping[str, Any]] = {}
    source_artifacts: list[dict[str, str]] = []
    if not isinstance(normalized_source_artifact_paths, Mapping):
        raise SparkAuditError("normalized Spark source path mapping required")
    checked_results = validate_bundle(spark_requests, spark_results, roles_cfg=roles_cfg)
    seen_audit_ids: set[str] = set()
    for result_index, result in enumerate(checked_results):
        if result.get("mission_id") != plan["mission_id"]:
            raise SparkAuditError("closure/Spark mission mismatch")
        audit_id = _id(result.get("audit_id"), f"$.spark_results[{result_index}].audit_id")
        artifact_sha256 = _hash64(
            result.get("artifact_sha256"),
            f"$.spark_results[{result_index}].artifact_sha256",
        )
        try:
            artifact_path = _relative_path(
                normalized_source_artifact_paths.get(audit_id),
                f"$.normalized_source_artifact_paths.{audit_id}",
            )
        except ContractError as exc:
            raise SparkAuditError(
                "normalized Spark source artifact path required"
            ) from exc
        source_artifacts.append({
            "audit_id": audit_id,
            "artifact_path": artifact_path,
            "artifact_sha256": artifact_sha256,
        })
        seen_audit_ids.add(audit_id)
        findings = result.get("findings")
        if not isinstance(findings, list):
            raise SparkAuditError(
                "Spark result findings array required",
                f"$.spark_results[{result_index}].findings",
            )
        for finding_index, finding in enumerate(findings):
            path = f"$.spark_results[{result_index}].findings[{finding_index}]"
            if not isinstance(finding, Mapping):
                raise SparkAuditError("Spark finding object required", path)
            finding_id = _id(finding.get("id"), f"{path}.id")
            if finding_id in source_findings:
                raise SparkAuditError("duplicate authoritative Spark finding", f"{path}.id")
            _safe_text(
                finding.get("counterexample"),
                f"{path}.counterexample",
            )
            source_findings[finding_id] = finding
    source_artifacts.sort(key=lambda item: item["audit_id"])
    if len({item["audit_id"] for item in source_artifacts}) != len(source_artifacts):
        raise SparkAuditError("duplicate authoritative Spark source artifact")
    if set(normalized_source_artifact_paths) != seen_audit_ids:
        raise SparkAuditError(
            "normalized Spark source path set differs from validated results"
        )

    plan_finding_ids = {item["finding_id"] for item in plan["findings"]}
    if set(source_findings) != plan_finding_ids:
        raise SparkAuditError("closure plan and authoritative Spark finding sets differ")
    bindings: list[dict[str, str]] = []
    stage_for_gate = milestone.gate_stage_map(cfg)
    for item in plan["findings"]:
        row_prefix = f"EVID-{item['gate_id']}-"
        if not item["evidence_row_id"].startswith(row_prefix):
            raise SparkAuditError("closure evidence row cannot resolve entrypoint")
        entrypoint_id = item["evidence_row_id"][len(row_prefix):]
        gate = gates.get(item["gate_id"])
        if (
            not entrypoint_id
            or gate is None
            or entrypoint_id not in entries
            or gate.get("stage") != item["stage"]
            or entrypoint_id not in gate.get("entrypoint_ids", [])
        ):
            raise SparkAuditError("closure binding is outside compiled mission plan")
        binding = {
            "finding_id": item["finding_id"],
            "counterexample_id": item["counterexample_id"],
            "executable_counterexample_id": item["executable_counterexample_id"],
            "counterexample_sha256": counterexample_sha256(
                source_findings[item["finding_id"]]["counterexample"],
                f"$.spark_findings.{item['finding_id']}.counterexample",
            ),
            "gate_id": item["gate_id"],
            "stage": item["stage"],
            "evidence_row_id": item["evidence_row_id"],
            "entrypoint_id": entrypoint_id,
            "binding_sha256": "",
        }
        binding["binding_sha256"] = canonical_sha256(binding)
        bindings.append(binding)
    bindings.sort(key=lambda item: item["finding_id"])
    receipt = {
        "schema": "closure-binding-receipt.v16",
        "mission_id": plan["mission_id"],
        "compiled_plan_sha256": compiled_plan_sha256,
        "closure_plan_sha256": plan["plan_sha256"],
        "closure_plan_file_sha256": closure_plan_file_sha256,
        "dispatch_transcript_file_sha256": dispatch_transcript_file_sha256,
        "normalized_source_artifacts": source_artifacts,
        "finding_count": len(bindings),
        "bindings": bindings,
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return validate_closure_binding_receipt(
        receipt,
        expected_compiled_plan_sha256=compiled_plan_sha256,
        expected_closure_plan_sha256=plan["plan_sha256"],
        expected_closure_plan_file_sha256=closure_plan_file_sha256,
        expected_dispatch_transcript_file_sha256=dispatch_transcript_file_sha256,
    )


def validate_author_closure(
    value: Any,
    *,
    root: str | pathlib.Path | None = None,
    evidence_rows: Sequence[Mapping[str, Any]] | None = None,
    milestone_cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the separate author-side closure for all actual 18 findings.

    The author closure is never allowed to rewrite the raw Spark disposition.
    It is a candidate-side claim, and is accepted only when every finding has
    a concrete executable counterexample, source location, gate/evidence row,
    and deterministic log hash.  Runtime callers additionally pass the actual
    evidence rows so the row/log bindings are checked rather than merely named.
    """
    # Trust-chain validators require a frozen milestone baseline.  This is
    # checked first so no structural error can mask the missing trust root.
    cfg = require_frozen(milestone_cfg)
    stage_for_gate = milestone.gate_stage_map(cfg)
    if not isinstance(value, dict):
        raise SparkAuditError("author closure object required")
    fields = {"schema", "mission_id", "candidate_head_sha", "candidate_tree_sha", "audited_head_sha", "plan_sha256", "finding_count", "findings", "disposition_summary", "artifact_sha256"}
    if set(value) != fields or value.get("schema") != "author-closure-final.v16" or value.get("mission_id") != "V16-PRODUCTIVITY":
        raise SparkAuditError("strict author closure fields")
    # Candidate/audited heads are Git object identities (40 hex); only the
    # plan and row/artifact bindings below use 64-hex SHA-256 digests.
    _sha(value["audited_head_sha"], "$.audited_head_sha"); _sha(value["candidate_head_sha"], "$.candidate_head_sha")
    if not isinstance(value["candidate_tree_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["candidate_tree_sha"]):
        raise SparkAuditError("candidate tree SHA required")
    _hash64(value["plan_sha256"], "$.plan_sha256")
    denom = author_closure_denominator(cfg)
    if denom is None:
        raise SparkAuditError("author closure denominator unavailable")
    author_ids = author_finding_ids(cfg)
    if value["finding_count"] != denom:
        raise SparkAuditError("author closure denominator must be 18")
    findings = value["findings"]
    if not isinstance(findings, list) or len(findings) != denom:
        raise SparkAuditError("author closure finding denominator")
    required_finding_fields = {"finding_id", "severity", "disposition", "counterexample_id", "executable_counterexample_id", "source_location", "gate_id", "stage", "evidence_row_id", "evidence_row_sha256", "log_sha256"}
    seen: set[str] = set()
    expected_rows: dict[str, Mapping[str, Any]] = {}
    if evidence_rows is not None:
        for row in evidence_rows:
            if not isinstance(row, Mapping) or row.get("case_id") in expected_rows:
                raise SparkAuditError("duplicate/malformed evidence row binding")
            expected_rows[str(row.get("case_id"))] = row
    for index, item in enumerate(findings):
        if not isinstance(item, dict) or set(item) != required_finding_fields:
            raise SparkAuditError("strict author closure finding fields", f"$.findings[{index}]")
        fid = item["finding_id"]
        if fid not in author_ids or fid in seen:
            raise SparkAuditError("author closure finding identity mismatch", f"$.findings[{index}].finding_id")
        seen.add(fid)
        if item["severity"] != "P1" or item["disposition"] not in {"FIXED", "DISAGREE"}:
            raise SparkAuditError("P1 author closure must be FIXED or DISAGREE", f"$.findings[{index}]")
        if item["counterexample_id"] not in {"CE-SCHEMA", "CE-GATE", "CE-EVIDENCE"} or not re.fullmatch(r"NF-[0-9]{3}", item["executable_counterexample_id"]):
            raise SparkAuditError("executable counterexample binding required", f"$.findings[{index}]")
        location = item["source_location"]
        if not isinstance(location, str) or ":" not in location or location.startswith(("/", "~")) or _PRIVATE_ARTIFACT_RE.search(location):
            raise SparkAuditError("portable current source location required", f"$.findings[{index}].source_location")
        if item["stage"] not in {"targeted", "full", "fresh"} or not re.fullmatch(r"G-(?:TARGETED|FULL|FRESH)", item["gate_id"]):
            raise SparkAuditError("gate/stage closure binding required", f"$.findings[{index}]")
        if item["stage"] != stage_for_gate.get(item["gate_id"]):
            raise SparkAuditError("gate/stage mismatch", f"$.findings[{index}]")
        if item["evidence_row_id"] != f"EVID-{item['gate_id']}-EP-UNIT":
            raise SparkAuditError("evidence row identity mismatch", f"$.findings[{index}].evidence_row_id")
        _hash64(item["log_sha256"], f"$.findings[{index}].log_sha256")
        _hash64(item["evidence_row_sha256"], f"$.findings[{index}].evidence_row_sha256")
        if evidence_rows is not None:
            row = expected_rows.get(item["evidence_row_id"])
            if row is None or row.get("log_sha256") != item["log_sha256"] or row.get("stage") != item["stage"] or row.get("gate_id") != item["gate_id"] or canonical_sha256(dict(row)) != item["evidence_row_sha256"]:
                raise SparkAuditError("author closure evidence/log binding mismatch", f"$.findings[{index}]")
    if seen != set(author_ids):
        raise SparkAuditError("all 18 actual findings require closure")
    summary = value["disposition_summary"]
    if summary != {"FIXED": denom, "DISAGREE": 0, "FOLLOW_UP": 0}:
        raise SparkAuditError("author closure summary must have no FOLLOW_UP")
    unsigned = dict(value); unsigned["artifact_sha256"] = ""
    if value["artifact_sha256"] != canonical_sha256(unsigned):
        raise SparkAuditError("author closure digest mismatch")
    if root is not None:
        root_path = pathlib.Path(root).resolve()
        try:
            current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
            current_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SparkAuditError("candidate identity unavailable") from exc
        if current_head != value["candidate_head_sha"] or current_tree != value["candidate_tree_sha"]:
            raise SparkAuditError("author closure candidate identity mismatch")
        for item in findings:
            rel = pathlib.PurePosixPath(item["source_location"].split(":", 1)[0])
            path = (root_path / rel).resolve()
            try:
                path.relative_to(root_path)
            except ValueError:
                raise SparkAuditError("closure source escapes candidate root")
            if not path.is_file() or path.is_symlink():
                raise SparkAuditError("closure source location missing")
    return dict(value)


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise SparkAuditError("boolean required", path)
    return value


def _safe_text(value: Any, path: str, *, max_len: int = 4096) -> str:
    """Validate finding prose while permitting literal domain words.

    Raw corrective counterexamples may accurately mention a dispatch
    transcript or an argv token.  Those words are not credentials; private
    absolute paths and control bytes remain forbidden.
    """
    value = _str(value, path, max_len=max_len, public=False)
    if _PRIVATE_ARTIFACT_RE.search(value):
        raise SparkAuditError("privacy-sensitive finding text", path)
    return value


def _timestamp(value: Any, path: str) -> str:
    value = _str(value, path, public=True)
    if not value.endswith("Z"):
        raise SparkAuditError("UTC timestamp required", path)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SparkAuditError("RFC3339 timestamp required", path) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SparkAuditError("UTC timestamp required", path)
    return value


def audit_requests(mission: Mapping[str, Any], roles_cfg: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    audits = mission.get("spark_audits")
    if not isinstance(audits, list) or len(audits) > MAX_SPARK_AUDITS:
        raise SparkAuditError("zero to three Spark audits required")
    mission_id = _id(mission["mission_id"], "$.mission_id")
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, audit in enumerate(audits):
        aid = _id(audit["id"], f"$.spark_audits[{index}].id")
        if aid in seen:
            raise SparkAuditError("duplicate Spark audit ID", f"$.spark_audits[{index}].id")
        seen.add(aid)
        requests.append({
            "schema": "spark-audit-request.v16", "audit_id": aid, "mission_id": mission_id,
            "domain": _str(audit["domain"], f"$.spark_audits[{index}].domain", public=True),
            "scope": list(audit["scope"]), "max_findings": _int(audit["max_findings"], f"$.spark_audits[{index}].max_findings", minimum=1, maximum=16),
            "assigned_model": _spark_model(roles_cfg), "role": "inner-auditor", "permissions": ["read"],
            "fork_turns": "none", "context_mode": "zero-context", "report_only": True, "spawn_index": index + 1,
        })
    return requests


def validate_request(value: Any, roles_cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("request object required")
    fields = {"schema", "audit_id", "mission_id", "domain", "scope", "max_findings", "assigned_model", "role", "permissions", "fork_turns", "context_mode", "report_only", "spawn_index"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional request fields")
    if value["schema"] != "spark-audit-request.v16":
        raise SparkAuditError("schema")
    if not _spark_model_matches(value["assigned_model"], roles_cfg):
        raise SparkAuditError("Spark model required")
    if value["role"] != "inner-auditor" or value["permissions"] != ["read"]:
        raise SparkAuditError("report-only read permission required")
    if value["fork_turns"] != "none" or value["context_mode"] != "zero-context" or value["report_only"] is not True:
        raise SparkAuditError("fresh report-only context required")
    _id(value["audit_id"], "$.audit_id"); _id(value["mission_id"], "$.mission_id"); _str(value["domain"], "$.domain", public=True)
    if not isinstance(value["scope"], list) or not value["scope"] or any(not isinstance(v, str) for v in value["scope"]):
        raise SparkAuditError("non-empty scope required", "$.scope")
    _int(value["max_findings"], "$.max_findings", minimum=1, maximum=16)
    _int(value["spawn_index"], "$.spawn_index", minimum=1, maximum=MAX_SPARK_AUDITS)
    return dict(value)


def _finding(value: Any, path: str, allowed_scope: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SparkAuditError("finding object required", path)
    fields = {"id", "severity", "label", "scope", "requirement", "location", "counterexample", "impact", "smallest_outcome", "acceptance_case", "attribution"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional finding fields", path)
    fid = _id(value["id"], f"{path}.id")
    severity = _str(value["severity"], f"{path}.severity", public=True)
    if severity not in {"P1", "P2", "P3"}:
        raise SparkAuditError("severity", f"{path}.severity")
    label = _str(value["label"], f"{path}.label", public=True)
    if label not in {"BLOCKING", "NON_BLOCKING", "NIT", "QUESTION", "FOLLOW_UP", "CONTRACT_CHALLENGE"}:
        raise SparkAuditError("finding label", f"{path}.label")
    if severity == "P1" and label != "BLOCKING":
        raise SparkAuditError("P1 must be BLOCKING", f"{path}.label")
    if severity != "P1" and label == "BLOCKING":
        raise SparkAuditError("only P1 may be BLOCKING", f"{path}.label")
    scope = _str(value["scope"], f"{path}.scope", public=True)
    if scope not in allowed_scope:
        raise SparkAuditError("finding out of audit scope", f"{path}.scope")
    return {"id": fid, "severity": severity, "label": label, "scope": scope, "requirement": _safe_text(value["requirement"], f"{path}.requirement"), "location": _safe_text(value["location"], f"{path}.location"), "counterexample": _safe_text(value["counterexample"], f"{path}.counterexample"), "impact": _safe_text(value["impact"], f"{path}.impact"), "smallest_outcome": _safe_text(value["smallest_outcome"], f"{path}.smallest_outcome"), "acceptance_case": _safe_text(value["acceptance_case"], f"{path}.acceptance_case"), "attribution": _safe_text(value["attribution"], f"{path}.attribution")}


def validate_result(value: Any, request: Mapping[str, Any], roles_cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request = validate_request(request, roles_cfg=roles_cfg)
    if not isinstance(value, dict):
        raise SparkAuditError("result object required")
    fields = {"schema", "audit_id", "mission_id", "task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "scope", "findings", "dispositions", "started_at", "ended_at", "elapsed_sec"}
    optional = {"artifact_sha256"}
    if set(value) - fields - optional or fields - set(value):
        raise SparkAuditError("missing/additional result fields")
    if value["schema"] != "spark-audit-result.v16":
        raise SparkAuditError("schema")
    if value["audit_id"] != request["audit_id"] or value["mission_id"] != request["mission_id"]:
        raise SparkAuditError("request/result identity mismatch")
    if not _spark_model_matches(value["assigned_model"], roles_cfg) or value["fork_turns"] != "none" or value["context_mode"] != "zero-context" or value["report_only"] is not True:
        raise SparkAuditError("Spark report-only identity required")
    task_id = _str(value["task_id"], "$.task_id", max_len=512, public=True)
    if not (task_id.startswith("/root/") or re.fullmatch(r"spark-task-[0-9]+", task_id)):
        raise SparkAuditError("canonical platform task path required", "$.task_id")
    if value["reasoning_effort"] != roles.effort_for("auditor_spark", roles_cfg):
        raise SparkAuditError("configured Spark reasoning effort required")
    scope = _str(value["scope"], "$.scope", public=True)
    if scope not in request["scope"]:
        raise SparkAuditError("result scope outside request")
    findings = value["findings"]
    if not isinstance(findings, list) or len(findings) > request["max_findings"]:
        raise SparkAuditError("finding count exceeds bounded request")
    checked = [_finding(item, f"$.findings[{i}]", request["scope"]) for i, item in enumerate(findings)]
    if len({f["id"] for f in checked}) != len(checked):
        raise SparkAuditError("duplicate findings")
    dispositions = value["dispositions"]
    if not isinstance(dispositions, dict) or set(dispositions) != {f["id"] for f in checked}:
        raise SparkAuditError("every finding must be dispositioned exactly once")
    if any(v not in {"FIXED", "DISAGREE", "FOLLOW_UP"} for v in dispositions.values()):
        raise SparkAuditError("invalid disposition")
    elapsed = value["elapsed_sec"]
    if type(elapsed) not in (int, float) or isinstance(elapsed, bool) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise SparkAuditError("finite elapsed required", "$.elapsed_sec")
    _timestamp(value["started_at"], "$.started_at"); _timestamp(value["ended_at"], "$.ended_at")
    started = datetime.fromisoformat(value["started_at"][:-1] + "+00:00")
    ended = datetime.fromisoformat(value["ended_at"][:-1] + "+00:00")
    if ended < started or abs(float(elapsed) - (ended - started).total_seconds()) > max(0.05, (ended - started).total_seconds() * 0.05):
        raise SparkAuditError("elapsed/timestamp mismatch", "$.elapsed_sec")
    if "artifact_sha256" in value:
        _hash64(value["artifact_sha256"], "$.artifact_sha256")
    result = dict(value); result["findings"] = checked; result["dispositions"] = dict(dispositions)
    return result


def validate_bundle(requests: Sequence[Mapping[str, Any]], results: Sequence[Mapping[str, Any]], *, budget: int = MAX_SPARK_AUDITS, roles_cfg: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    if type(budget) is not int or not 0 <= budget <= MAX_SPARK_AUDITS:
        raise SparkAuditError("Spark spawn budget must be in [0,3]")
    if len(requests) > budget or len(results) != len(requests):
        raise SparkAuditError("Spark results must exactly match the bounded request set")
    checked_requests = [validate_request(r, roles_cfg=roles_cfg) for r in requests]
    if len({r["audit_id"] for r in checked_requests}) != len(checked_requests):
        raise SparkAuditError("duplicate Spark audit request")
    by_id = {r["audit_id"]: r for r in checked_requests}
    checked_results = []
    for result in results:
        aid = result.get("audit_id") if isinstance(result, dict) else None
        if aid not in by_id:
            raise SparkAuditError("missing/duplicate/out-of-scope Spark result")
        if any(r.get("audit_id") == aid for r in checked_results):
            raise SparkAuditError("duplicate Spark result")
        checked_results.append(validate_result(result, by_id[aid], roles_cfg=roles_cfg))
    if {r["audit_id"] for r in checked_results} != set(by_id):
        raise SparkAuditError("missing Spark result")
    return checked_results


def validate_dispatch_transcript(
    value: Any,
    *,
    expected_head: str | None = None,
    expected_tree: str | None = None,
    root: str | pathlib.Path | None = None,
    milestone_cfg: Mapping[str, Any] | None = None,
    roles_cfg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the immutable Route-2 dispatch transcript.

    Historical records are retained as explicitly unverified facts.  Exactly
    three replacement task identities are accepted, each with request,
    canonical response, snapshot, two-run ordering, result, and disposition
    hashes.  Backend instance/run IDs may be ``unavailable`` only because the
    control plane does not expose them; every other identity is mandatory.
    """
    # Trust-chain validators require a frozen milestone baseline.  This is
    # checked first so no structural error can mask the missing trust root.
    cfg = require_frozen(milestone_cfg)
    if not isinstance(value, dict):
        raise SparkAuditError("dispatch transcript object required")
    fields = {"schema", "transcript_version", "lineage_mode", "mission_id", "base_sha", "base_tree", "mission_scope_sha256", "compiled_plan_sha256", "reviewed_head_sha", "reviewed_tree_sha", "snapshot", "audited_input_snapshot", "candidate_binding", "historical_original_audits", "accepted_current_audits", "finding_dispositions", "ordering", "historical_spawn_count", "accepted_current_spawn_count", "transcript_sha256"}
    if set(value) != fields:
        raise SparkAuditError("missing/additional transcript fields")
    if value["schema"] != "dispatch-transcript.v16" or type(value["transcript_version"]) is not int or value["transcript_version"] < 2 or value["lineage_mode"] != "DISPATCH_TRANSCRIPT":
        raise SparkAuditError("transcript schema/version")
    _id(value["mission_id"], "$.mission_id")
    expected_base_sha, expected_base_tree = milestone.base_identity(cfg)
    if expected_base_sha is None or expected_base_tree is None:
        raise SparkAuditError("frozen base identity unavailable")
    base = _sha(value["base_sha"], "$.base_sha"); base_tree = _sha(value["base_tree"], "$.base_tree"); head = _sha(value["reviewed_head_sha"], "$.reviewed_head_sha"); tree = _sha(value["reviewed_tree_sha"], "$.reviewed_tree_sha")
    if base != expected_base_sha or base_tree != expected_base_tree:
        raise SparkAuditError("frozen base identity changed")
    _hash64(value["mission_scope_sha256"], "$.mission_scope_sha256")
    _hash64(value["compiled_plan_sha256"], "$.compiled_plan_sha256")
    pins = milestone.transcript_config(cfg)
    expected_mission_scope = pins["mission_scope_sha256"]
    expected_compiled_plan = pins["compiled_plan_sha256"]
    if (expected_mission_scope is not None and value["mission_scope_sha256"] != expected_mission_scope) or (
        expected_compiled_plan is not None and value["compiled_plan_sha256"] != expected_compiled_plan
    ):
        raise SparkAuditError("mission scope/compiled plan identity changed")
    # ``reviewed_head_sha``/``reviewed_tree_sha`` are the immutable audited
    # input snapshot.  ``expected_head``/``expected_tree`` identify the runtime
    # candidate supplied by the caller and are intentionally checked only in
    # the repository-backed binding below; equating them here would make the
    # committed transcript self-referential and defeat the transition contract.
    audited = value["audited_input_snapshot"]
    if not isinstance(audited, dict) or set(audited) != {"identity_mode", "head_sha", "tree_sha", "path_hash_set_sha256"}:
        raise SparkAuditError("strict audited input snapshot fields", "$.audited_input_snapshot")
    if audited["identity_mode"] != "git-exact-object":
        raise SparkAuditError("audited input identity mode", "$.audited_input_snapshot.identity_mode")
    audited_head = _sha(audited["head_sha"], "$.audited_input_snapshot.head_sha")
    audited_tree = _sha(audited["tree_sha"], "$.audited_input_snapshot.tree_sha")
    _hash64(audited["path_hash_set_sha256"], "$.audited_input_snapshot.path_hash_set_sha256")
    # The audited remediation parent is additionally pinned by value when the
    # milestone carries one; otherwise it is bound only by the reviewed==audited
    # snapshot equality below and, when *root* is supplied, by the git ancestry
    # chain base -> audited_head -> candidate.
    expected_audited_parent = pins["audited_parent_sha"]
    if expected_audited_parent is not None and audited_head != expected_audited_parent:
        raise SparkAuditError("audited remediation parent identity changed", "$.audited_input_snapshot")
    binding = value["candidate_binding"]
    if not isinstance(binding, dict) or set(binding) != {"mode", "required", "relation"}:
        raise SparkAuditError("strict candidate binding fields", "$.candidate_binding")
    if binding["mode"] != "external_evidence_envelope" or binding["required"] is not True or binding["relation"] != "descendant_of_audited_input":
        raise SparkAuditError("candidate transition binding mismatch", "$.candidate_binding")
    snapshot = value["snapshot"]
    if not isinstance(snapshot, dict) or set(snapshot) != {"identity_mode", "head_sha", "tree_sha", "path_count", "path_hash_set_sha256", "path_hash_set_artifact", "path_hash_set_artifact_sha256"}:
        raise SparkAuditError("strict snapshot fields", "$.snapshot")
    if snapshot["identity_mode"] != "git-exact-object" or snapshot["head_sha"] != head or snapshot["tree_sha"] != tree:
        raise SparkAuditError("snapshot identity mismatch", "$.snapshot")
    if head != audited_head or tree != audited_tree or snapshot["path_hash_set_sha256"] != audited["path_hash_set_sha256"]:
        raise SparkAuditError("reviewed snapshot must equal audited input snapshot", "$.snapshot")
    _int(snapshot["path_count"], "$.snapshot.path_count", minimum=1); _hash64(snapshot["path_hash_set_sha256"], "$.snapshot.path_hash_set_sha256"); _str(snapshot["path_hash_set_artifact"], "$.snapshot.path_hash_set_artifact", public=True); _hash64(snapshot["path_hash_set_artifact_sha256"], "$.snapshot.path_hash_set_artifact_sha256")
    historical = value["historical_original_audits"]; current = value["accepted_current_audits"]
    if not isinstance(historical, list) or len(historical) != 3 or not isinstance(current, list) or len(current) != 3:
        raise SparkAuditError("exactly three historical and three accepted audits required")
    def check_common(item: Mapping[str, Any], path: str) -> None:
        if not isinstance(item, dict): raise SparkAuditError("audit record object required", path)
        for key in ("audit_id", "task_id", "parent_task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode"):
            _str(item.get(key), f"{path}.{key}", public=True)
        _str(item["task_id"], f"{path}.task_id", max_len=512, public=True)
        if not _spark_model_matches(item["assigned_model"], roles_cfg) or item["reasoning_effort"] != roles.effort_for("auditor_spark", roles_cfg) or item["fork_turns"] != "none" or item["context_mode"] != "zero-context" or item.get("report_only") is not True:
            raise SparkAuditError("Spark identity mismatch", path)
    expected_historical_records = expected_historical(cfg)
    expected_current_records = expected_current(cfg)
    for i, item in enumerate(historical):
        check_common(item, f"$.historical_original_audits[{i}]")
        if set(item) != {"audit_id", "global_spawn_index", "task_id", "parent_task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "status", "accepted", "finding_ids", "missing_facts"}:
            raise SparkAuditError("strict historical audit fields", f"$.historical_original_audits[{i}]")
        expected_id, expected_task, expected_findings = expected_historical_records[i]
        if (item["audit_id"], item["task_id"], item["finding_ids"]) != (expected_id, expected_task, expected_findings):
            raise SparkAuditError("historical task identity changed", f"$.historical_original_audits[{i}]")
        if item.get("global_spawn_index") != i + 1 or item.get("status") != "HISTORICAL_UNVERIFIED" or item.get("accepted") is not False:
            raise SparkAuditError("historical audit identity/status mismatch", f"$.historical_original_audits[{i}]")
        if not isinstance(item.get("finding_ids"), list) or not item["finding_ids"] or not isinstance(item.get("missing_facts"), list) or not item["missing_facts"]:
            raise SparkAuditError("historical missing-fact record required", f"$.historical_original_audits[{i}]")
    task_ids: set[str] = set()
    expected_raw_sha = expected_raw_platform_sha256(cfg)
    expected_norm_ids = normalized_finding_ids(cfg)
    stage_for_gate = milestone.gate_stage_map(cfg)
    for i, item in enumerate(current):
        path = f"$.accepted_current_audits[{i}]"; check_common(item, path)
        if set(item) != {"audit_id", "global_spawn_index", "accepted_spawn_index", "task_id", "parent_task_id", "assigned_model", "reasoning_effort", "fork_turns", "context_mode", "report_only", "scope", "spawn_request_sha256", "spawn_response_sha256", "canonical_response_task_id", "initial_run", "corrective_run", "result_artifact_sha256", "sender_final_envelope_sha256", "snapshot_path_hash_set_sha256", "completion_before_first_production_fix", "finding_ids", "finding_dispositions", "normalized_artifact_path", "normalized_artifact_sha256", "raw_platform_sha256", "author_closure_plan_path", "author_closure_plan_sha256"}:
            raise SparkAuditError("strict accepted audit fields", path)
        if item.get("global_spawn_index") != i + 4 or item.get("accepted_spawn_index") != i + 1 or item["task_id"] in task_ids:
            raise SparkAuditError("accepted spawn index/task identity mismatch", path)
        task_ids.add(item["task_id"])
        expected = expected_current_records[i]
        for key in ("audit_id", "task_id", "parent_task_id", "scope", "finding_ids", "spawn_request_sha256", "spawn_response_sha256", "snapshot_path_hash_set_sha256"):
            if item.get(key) != expected[key]:
                raise SparkAuditError("accepted audit fact changed", f"{path}.{key}")
        if item.get("canonical_response_task_id") != item["task_id"]:
            raise SparkAuditError("canonical response task mismatch", f"{path}.canonical_response_task_id")
        for key in ("spawn_request_sha256", "spawn_response_sha256", "result_artifact_sha256", "sender_final_envelope_sha256", "snapshot_path_hash_set_sha256", "normalized_artifact_sha256", "raw_platform_sha256", "author_closure_plan_sha256"):
            _hash64(item.get(key), f"{path}.{key}")
        if item["snapshot_path_hash_set_sha256"] != snapshot["path_hash_set_sha256"]:
            raise SparkAuditError("child snapshot hash mismatch", path)
        if item["snapshot_path_hash_set_sha256"] != audited["path_hash_set_sha256"]:
            raise SparkAuditError("accepted result is not bound to audited input snapshot", path)
        for run_name in ("initial_run", "corrective_run"):
            run = item.get(run_name)
            if not isinstance(run, dict) or set(run) != {"request_sha256", "final_envelope_sha256", "status", "run_order"}:
                raise SparkAuditError("strict child run fields", f"{path}.{run_name}")
            _hash64(run["request_sha256"], f"{path}.{run_name}.request_sha256"); _hash64(run["final_envelope_sha256"], f"{path}.{run_name}.final_envelope_sha256")
            _int(run["run_order"], f"{path}.{run_name}.run_order", minimum=1)
        if item["initial_run"]["status"] != "INVALID_AUDIT" or item["corrective_run"]["status"] != "AUDIT_COMPLETE" or item["corrective_run"]["run_order"] != 2:
            raise SparkAuditError("initial/corrective run ordering required", path)
        if item["initial_run"]["run_order"] != 1:
            raise SparkAuditError("initial run must precede corrective run", path)
        if item["result_artifact_sha256"] != item["corrective_run"]["final_envelope_sha256"] or item["sender_final_envelope_sha256"] != item["result_artifact_sha256"]:
            raise SparkAuditError("result/final envelope hash mismatch", path)
        if "backend_instance_id" in item or "backend_run_id" in item:
            raise SparkAuditError("backend IDs are not present in the dispatch transcript", path)
        if item["initial_run"]["request_sha256"] != expected["initial_request_sha256"] or item["initial_run"]["final_envelope_sha256"] != expected["initial_final_envelope_sha256"] or item["corrective_run"]["request_sha256"] != expected["corrective_request_sha256"] or item["corrective_run"]["final_envelope_sha256"] != expected["corrective_final_envelope_sha256"]:
            raise SparkAuditError("corrective audit facts changed", path)
        if item.get("completion_before_first_production_fix") is not True:
            raise SparkAuditError("audit completion must precede production fix", path)
        finding_dispositions = item.get("finding_dispositions")
        if not isinstance(finding_dispositions, dict) or set(finding_dispositions) != set(item["finding_ids"]) or any(value != "FOLLOW_UP" for value in finding_dispositions.values()):
            raise SparkAuditError("raw current finding FOLLOW_UP disposition binding required", path)
        if item["normalized_artifact_path"] != f"zgov/v16/contracts/spark_result_{i + 1 and chr(65 + i)}.v16.json" or item["author_closure_plan_path"] != "zgov/v16/contracts/author_closure_plan.v16.json":
            raise SparkAuditError("normalized/closure-plan path binding changed", path)
        if item["raw_platform_sha256"] != expected_raw_sha[item["audit_id"]]:
            raise SparkAuditError("raw platform hash binding changed", path)
        expected_normalized_ids = expected_norm_ids[item["audit_id"]]
        if item["finding_ids"] != expected_normalized_ids:
            raise SparkAuditError("actual normalized finding IDs changed", path)
        expected_plan_sha = expected.get("author_closure_plan_sha256")
        if expected_plan_sha is None:
            expected_plan_sha = pins["author_closure_plan_sha256"]
        if expected_plan_sha is not None and item["author_closure_plan_sha256"] != expected_plan_sha:
            raise SparkAuditError("closure plan hash binding changed", path)
    if value["historical_spawn_count"] != 6 or value["accepted_current_spawn_count"] != 3:
        raise SparkAuditError("historical/current spawn denominator mismatch")
    if set(task_ids) != {item["task_id"] for item in current}:
        raise SparkAuditError("duplicate accepted task IDs")
    dispositions = value["finding_dispositions"]
    historical_finding_ids = historical_findings(cfg)
    if not isinstance(dispositions, dict) or set(dispositions) != set(historical_finding_ids):
        raise SparkAuditError("all nineteen individual finding dispositions required", "$.finding_dispositions")
    if any(v not in {"HISTORICAL_UNVERIFIED", "FOLLOW_UP", "FIXED", "DISAGREE", "INFRA_BLOCKED"} for v in dispositions.values()):
        raise SparkAuditError("invalid finding disposition", "$.finding_dispositions")
    if any(dispositions[fid] != "HISTORICAL_UNVERIFIED" for fid in historical_finding_ids):
        raise SparkAuditError("historical finding dispositions are immutable", "$.finding_dispositions")
    ordering = value["ordering"]
    if not isinstance(ordering, dict) or ordering.get("baseline_reproduction_completed_before_first_edit") is not True or ordering.get("replacement_audits_completed_before_first_production_fix") is not True:
        raise SparkAuditError("completion ordering mismatch", "$.ordering")
    digest_value = dict(value); digest_value["transcript_sha256"] = ""
    expected_digest = hashlib.sha256(canonical_json(digest_value).encode("utf-8")).hexdigest()
    if value["transcript_sha256"] != expected_digest:
        raise SparkAuditError("transcript SHA mismatch", "$.transcript_sha256")
    if root is not None:
        root_path = pathlib.Path(root).resolve()
        if expected_head is None or expected_tree is None:
            raise SparkAuditError("runtime candidate head/tree required when root is supplied")
        try:
            current_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
            current_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SparkAuditError("candidate Git identity unavailable") from exc
        if current_head != expected_head or current_tree != expected_tree:
            raise SparkAuditError("runtime candidate identity mismatch")
        audited_object_tree = subprocess.check_output(["git", "rev-parse", f"{audited_head}^{{tree}}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        if audited_object_tree != audited_tree:
            raise SparkAuditError("audited head/tree object mismatch")
        base_object_tree = subprocess.check_output(["git", "rev-parse", f"{base}^{{tree}}"], cwd=str(root_path), text=True, stderr=subprocess.PIPE).strip()
        if base_object_tree != base_tree:
            raise SparkAuditError("base head/tree object mismatch")
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, audited_head], cwd=str(root_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode != 0:
            raise SparkAuditError("audited input is not a descendant of frozen base")
        if subprocess.run(["git", "merge-base", "--is-ancestor", audited_head, expected_head], cwd=str(root_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode != 0:
            raise SparkAuditError("candidate is not a descendant of audited input snapshot")
        mission_path = root_path / "zgov/v16/fixtures/mission.valid.json"
        try:
            mission = json.loads(mission_path.read_text(encoding="utf-8"))
            from .contracts import canonical_sha256, validate_counterexample_linkage, validate_mission
            checked_mission = validate_mission(mission); validate_counterexample_linkage(checked_mission)
            if checked_mission["scope"]["exact_head"] != base or checked_mission["scope"].get("tree_sha") != base_tree or canonical_sha256(checked_mission["scope"]) != value["mission_scope_sha256"]:
                raise SparkAuditError("mission scope binding mismatch")
            # The transcript records the plan produced for the immutable
            # audited input.  Recompiling that mission with the candidate's
            # newer compiler would conflate historical lineage with current
            # execution semantics.  The current compiled plan is bound
            # separately by the pre-execution closure receipt and authority.
        except SparkAuditError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SparkAuditError("mission scope/plan binding unavailable") from exc
        manifest_path = root_path / "manifest.json"
        transcript_path = root_path / "zgov/v16/contracts/v16_dispatch_transcript.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            declared_hash = manifest["files"]["zgov/v16/contracts/v16_dispatch_transcript.json"]
            actual_hash = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise SparkAuditError("manifest/transcript binding unavailable") from exc
        if declared_hash != actual_hash:
            raise SparkAuditError("manifest transcript hash mismatch")
        snapshot_artifact = root_path / snapshot["path_hash_set_artifact"]
        if snapshot_artifact.is_symlink() or not snapshot_artifact.is_file() or hashlib.sha256(snapshot_artifact.read_bytes()).hexdigest() != snapshot["path_hash_set_artifact_sha256"]:
            raise SparkAuditError("snapshot path/hash-set artifact mismatch")
        if hashlib.sha256(snapshot_artifact.read_bytes()).hexdigest() != snapshot["path_hash_set_sha256"]:
            raise SparkAuditError("snapshot path/hash-set content digest mismatch")
        for index, item in enumerate(current):
            normalized_path = root_path / item["normalized_artifact_path"]
            if normalized_path.is_symlink() or not normalized_path.is_file() or hashlib.sha256(normalized_path.read_bytes()).hexdigest() != item["normalized_artifact_sha256"]:
                raise SparkAuditError("normalized Spark artifact hash/path mismatch", f"$.accepted_current_audits[{index}]")
            try:
                normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SparkAuditError("normalized Spark artifact unreadable", f"$.accepted_current_audits[{index}]") from exc
            _validate_normalized_result(normalized, audit_id=item["audit_id"], expected_raw_sha=item["raw_platform_sha256"], milestone_cfg=cfg)
            plan_path = root_path / item["author_closure_plan_path"]
            if plan_path.is_symlink() or not plan_path.is_file() or hashlib.sha256(plan_path.read_bytes()).hexdigest() != item["author_closure_plan_sha256"]:
                raise SparkAuditError("author closure plan hash/path mismatch", f"$.accepted_current_audits[{index}]")
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SparkAuditError("author closure plan unreadable", f"$.accepted_current_audits[{index}]") from exc
            validate_author_closure_plan(plan, root=root_path, milestone_cfg=cfg)
    return dict(value)


validate_spark_request = validate_request
validate_spark_result = validate_result
