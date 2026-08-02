"""Shared helpers so gov tests are re-entrant across roles configurations.

Model identities in ``gov/zgov`` are configuration, not code.  A test fixture
that hard-codes a model name only passes while the roles config is unresolved
(the modules skip identity assertions on placeholders) and breaks the moment a
real ``roles.json`` is installed.  These helpers derive every model identity
from the *current* roles configuration instead.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from zgov import roles
from zgov.review_policy import resolve_reviewer

# The single place in the test suite that names concrete models.  Values are
# arbitrary; nothing asserts against these literals, they only have to be
# non-placeholder and mutually distinct.
RESOLVED_TEST_ROLES: dict[str, str] = {
    "writer": "zgov-test-writer",
    "executor": "zgov-test-executor",
    "reviewer_standard": "zgov-test-reviewer-standard",
    "reviewer_high": "zgov-test-reviewer-high",
    "auditor_spark": "zgov-test-auditor-spark",
}

# The placeholder counterpart.  This must not be derived from
# ``roles.DEFAULT_ROLES``: the shipped defaults are real model identities, so
# reading them here would silently turn every ``resolved=False`` fixture into a
# resolved one and disable the placeholder half of the suite.
PLACEHOLDER_TEST_ROLES: dict[str, str] = {
    "writer": "<TBD:writer>",
    "executor": "<TBD:executor>",
    "reviewer_standard": "<TBD:reviewer_standard>",
    "reviewer_high": "<TBD:reviewer_high>",
    "auditor_spark": "<TBD:auditor_spark>",
}


def roles_config(*, resolved: bool = True, efforts: dict[str, str] | None = None) -> dict[str, Any]:
    """Return a valid roles config, either fully resolved or all placeholders."""
    role_values = dict(RESOLVED_TEST_ROLES if resolved else PLACEHOLDER_TEST_ROLES)
    return {
        "schema": roles.ROLES_SCHEMA,
        "roles": role_values,
        "efforts": dict(roles.DEFAULT_ROLES["efforts"] if efforts is None else efforts),
        "agents": {},
    }


def pinned_roles(
    testcase,
    *,
    resolved: bool = True,
    overrides: dict[str, str] | None = None,
    efforts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Point ``ZGOV_ROLES_PATH`` at a throwaway roles file for one test.

    Restores the previous environment and removes the temporary directory via
    ``testcase.addCleanup``.  Returns the roles config that was written.
    """
    config = roles_config(resolved=resolved, efforts=efforts)
    if overrides:
        config["roles"].update(overrides)

    tmpdir = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmpdir.cleanup)
    path = Path(tmpdir.name) / "roles.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    previous = os.environ.get("ZGOV_ROLES_PATH")

    def restore() -> None:
        if previous is None:
            os.environ.pop("ZGOV_ROLES_PATH", None)
        else:
            os.environ["ZGOV_ROLES_PATH"] = previous

    testcase.addCleanup(restore)
    os.environ["ZGOV_ROLES_PATH"] = str(path)
    return config


def effort_for_risk(risk: str, *, delta: bool = False) -> str:
    """Return the reasoning effort a reviewer identity carries under live roles.

    Reasoning effort is configuration exactly like the model identity, so a
    fixture that hard-codes ``"xhigh"`` only passes while the installed
    ``roles.json`` happens to use the default efforts.  Route the mapping
    through here so no test restates it.
    """
    if delta:
        return roles.effort_for("delta_continuation")
    if risk == "high":
        return roles.effort_for("reviewer_high")
    if risk in {"low", "medium"}:
        return roles.effort_for("reviewer_standard")
    raise ValueError(f"review risk must be low/medium/high: {risk}")


def mission_review_risk(mission: dict[str, Any]) -> str:
    """Return the risk that selects a mission's independent reviewer.

    Mirrors ``zgov.contracts.validate_mission``: an absent policy takes the
    legacy fail-closed high-risk route.
    """
    policy = mission.get("review_policy")
    if isinstance(policy, dict) and isinstance(policy.get("review_risk"), str):
        return policy["review_risk"]
    return "high"


def mission_with_current_reviewer(mission: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
    """Deep-copy *mission* with its reviewer identity taken from live roles.

    ``reviewer_separation.independent_model`` is the only model-identity field
    the mission contract binds; ``assigned_model`` is free text there.  Pass
    *model* to inject a deliberately wrong identity for negative tests.
    """
    copy = json.loads(json.dumps(mission))
    if model is None:
        model = resolve_reviewer(mission_review_risk(copy))["model"]
    if isinstance(copy.get("reviewer_separation"), dict):
        copy["reviewer_separation"]["independent_model"] = model
    return copy
