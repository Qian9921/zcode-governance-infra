"""Unit tests for gov/zgov/roles.py and gov/zgov/milestone.py."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import support

from zgov import roles
from zgov import milestone

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLES_EXAMPLE = REPO_ROOT / "gov" / "roles.example.json"


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _frontmatter_name(path: Path) -> str:
    """Return the ``name:`` value from a markdown file's YAML frontmatter block."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise AssertionError(f"{path}: missing frontmatter block")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            value = line.split(":", 1)[1].strip()
            if not value:
                raise AssertionError(f"{path}: empty name in frontmatter")
            return value
    raise AssertionError(f"{path}: no name field in frontmatter")


class EnvPatcher:
    """Patch environment variables and restore them on cleanup."""

    def __init__(self, **values: str | None):
        self.values = values
        self.previous: dict[str, str | None] = {}

    def apply(self) -> None:
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def restore(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class RolesTests(unittest.TestCase):
    """Tests for gov/zgov/roles.py."""

    def _patch_env(self, **values: str | None) -> EnvPatcher:
        patcher = EnvPatcher(**values)
        patcher.apply()
        self.addCleanup(patcher.restore)
        return patcher

    def test_placeholder_file_load_unresolved(self) -> None:
        """An all-placeholder roles file loads and reports as unresolved.

        The fixture is pinned explicitly rather than taken from the built-in
        fallback: the shipped defaults are real model identities, so relying on
        them would make this case depend on what ships, not on placeholder
        semantics.
        """
        support.pinned_roles(self, resolved=False)
        cfg = roles.load_roles()
        self.assertEqual(cfg["schema"], roles.ROLES_SCHEMA)
        for name in roles.ROLE_NAMES:
            self.assertTrue(roles.is_placeholder(cfg["roles"][name]))
        self.assertFalse(roles.roles_resolved(cfg))

    def test_resolve_placeholder_raises(self) -> None:
        """resolve_role raises RolePlaceholderUnresolved with reason code."""
        support.pinned_roles(self, resolved=False)
        with self.assertRaises(roles.RolePlaceholderUnresolved) as ctx:
            roles.resolve_role("writer")
        self.assertIn("ROLE_PLACEHOLDER_UNRESOLVED", str(ctx.exception))

    def test_require_roles_resolved_raises_under_placeholders(self) -> None:
        """require_roles_resolved names every unresolved role."""
        support.pinned_roles(self, resolved=False)
        with self.assertRaises(roles.RolePlaceholderUnresolved) as ctx:
            roles.require_roles_resolved()
        message = str(ctx.exception)
        self.assertIn("ROLE_PLACEHOLDER_UNRESOLVED", message)
        for name in roles.ROLE_NAMES:
            self.assertIn(name, message)

    def test_shipped_defaults_are_fully_resolved(self) -> None:
        """The built-in fallback ships real identities, not placeholders.

        Locks the current fact in place: if anyone reverts DEFAULT_ROLES to
        ``<TBD:*>`` placeholders, this fails immediately.
        """
        self.assertIs(roles.roles_resolved(roles.DEFAULT_ROLES), True)
        for name in roles.ROLE_NAMES:
            value = roles.DEFAULT_ROLES["roles"][name]
            self.assertIsInstance(value, str)
            self.assertTrue(value)
            self.assertFalse(roles.is_placeholder(value))
        # The fallback path (file absent) hands back those same resolved values.
        self._patch_env(ZGOV_ROLES_PATH="/nonexistent/roles.json")
        cfg = roles.load_roles()
        self.assertEqual(cfg, roles.validate_roles(roles.DEFAULT_ROLES))
        self.assertTrue(roles.roles_resolved(cfg))
        for name in roles.ROLE_NAMES:
            self.assertEqual(
                roles.resolve_role(name, cfg), roles.DEFAULT_ROLES["roles"][name]
            )

    def test_roles_example_matches_built_in_defaults(self) -> None:
        """gov/roles.example.json and DEFAULT_ROLES are byte-identical canonically."""
        example = json.loads(ROLES_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(_canonical(example), _canonical(roles.DEFAULT_ROLES))
        self.assertEqual(
            _canonical(roles.validate_roles(example)),
            _canonical(roles.validate_roles(roles.DEFAULT_ROLES)),
        )

    def test_agents_mapping_files_exist_with_matching_names(self) -> None:
        """Every DEFAULT_ROLES agent maps to a real gov/agents/<name>.md whose
        frontmatter ``name:`` equals the agent name; the file set matches exactly.

        Locks the roles agents mapping to the actual agent files: changing one
        side without the other makes this fail immediately.
        """
        agents_dir = REPO_ROOT / "gov" / "agents"
        mapped = set(roles.DEFAULT_ROLES["agents"].values())
        self.assertEqual(len(mapped), 3)
        for agent in mapped:
            path = agents_dir / f"{agent}.md"
            self.assertTrue(path.is_file(), f"missing agent file for {agent}: {path}")
            self.assertEqual(
                _frontmatter_name(path),
                agent,
                f"{path}: frontmatter name does not match agent {agent}",
            )
        actual = {p.stem for p in agents_dir.glob("*.md")}
        self.assertEqual(
            actual,
            mapped,
            "gov/agents/ .md files must equal the set of DEFAULT_ROLES agents",
        )

    def test_filled_roles_resolve(self) -> None:
        """A fully-filled roles file resolves every role correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.json"
            filled = {
                "schema": roles.ROLES_SCHEMA,
                "roles": {
                    "writer": "zcode-writer-model",
                    "executor": "zcode-executor-model",
                    "reviewer_standard": "zcode-reviewer-standard-model",
                    "reviewer_high": "zcode-reviewer-high-model",
                    "auditor_spark": "zcode-auditor-spark-model",
                },
                "efforts": {
                    "reviewer_standard": "high",
                    "reviewer_high": "xhigh",
                    "delta_continuation": "high",
                    "auditor_spark": "high",
                },
                "agents": {
                    "reviewer_standard": "gov-reviewer",
                    "reviewer_high": "gov-reviewer-high",
                    "executor": "gov-executor",
                    "auditor_spark": "spark-agent",
                },
            }
            path.write_text(json.dumps(filled), encoding="utf-8")
            self._patch_env(ZGOV_ROLES_PATH=str(path))
            cfg = roles.load_roles()
            self.assertTrue(roles.roles_resolved(cfg))
            self.assertEqual(roles.resolve_role("writer", cfg), "zcode-writer-model")
            self.assertEqual(roles.resolve_role("executor", cfg), "zcode-executor-model")
            self.assertEqual(
                roles.resolve_role("reviewer_standard", cfg),
                "zcode-reviewer-standard-model",
            )
            self.assertEqual(
                roles.resolve_role("reviewer_high", cfg), "zcode-reviewer-high-model"
            )
            self.assertEqual(
                roles.resolve_role("auditor_spark", cfg), "zcode-auditor-spark-model"
            )

    def test_extra_top_level_property(self) -> None:
        """An unknown top-level key raises RoleError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": roles.ROLES_SCHEMA,
                        "roles": dict(roles.DEFAULT_ROLES["roles"]),
                        "efforts": dict(roles.DEFAULT_ROLES["efforts"]),
                        "agents": dict(roles.DEFAULT_ROLES["agents"]),
                        "extra_field": "bad",
                    }
                ),
                encoding="utf-8",
            )
            self._patch_env(ZGOV_ROLES_PATH=str(path))
            with self.assertRaises(roles.RoleError) as ctx:
                roles.load_roles()
            self.assertIn("extra_field", str(ctx.exception))

    def test_roles_missing_key(self) -> None:
        """A missing role key raises RoleError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.json"
            bad_roles = dict(roles.DEFAULT_ROLES["roles"])
            del bad_roles["writer"]
            path.write_text(
                json.dumps(
                    {
                        "schema": roles.ROLES_SCHEMA,
                        "roles": bad_roles,
                        "efforts": dict(roles.DEFAULT_ROLES["efforts"]),
                    }
                ),
                encoding="utf-8",
            )
            self._patch_env(ZGOV_ROLES_PATH=str(path))
            with self.assertRaises(roles.RoleError) as ctx:
                roles.load_roles()
            self.assertIn("roles", str(ctx.exception))

    def test_effort_placeholder_rejected(self) -> None:
        """Effort values cannot be placeholders."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.json"
            bad_efforts = dict(roles.DEFAULT_ROLES["efforts"])
            bad_efforts["reviewer_standard"] = "<TBD:effort>"
            path.write_text(
                json.dumps(
                    {
                        "schema": roles.ROLES_SCHEMA,
                        "roles": dict(roles.DEFAULT_ROLES["roles"]),
                        "efforts": bad_efforts,
                    }
                ),
                encoding="utf-8",
            )
            self._patch_env(ZGOV_ROLES_PATH=str(path))
            with self.assertRaises(roles.RoleError) as ctx:
                roles.load_roles()
            self.assertIn("effort", str(ctx.exception).lower())

    def test_schema_mismatch(self) -> None:
        """An incorrect schema raises RoleError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "wrong-schema",
                        "roles": dict(roles.DEFAULT_ROLES["roles"]),
                        "efforts": dict(roles.DEFAULT_ROLES["efforts"]),
                    }
                ),
                encoding="utf-8",
            )
            self._patch_env(ZGOV_ROLES_PATH=str(path))
            with self.assertRaises(roles.RoleError) as ctx:
                roles.load_roles()
            self.assertIn("schema", str(ctx.exception))


class MilestoneTests(unittest.TestCase):
    """Tests for gov/zgov/milestone.py."""

    def _patch_env(self, **values: str | None) -> EnvPatcher:
        patcher = EnvPatcher(**values)
        patcher.apply()
        self.addCleanup(patcher.restore)
        return patcher

    def _valid_frozen(self) -> dict:
        return {
            "schema": milestone.MILESTONE_SCHEMA,
            "frozen": True,
            "milestone_id": "M1",
            "repo": "zcode-governance-infra",
            "base_sha": "e18439c8dfe01d901895efd09b8b73b6842327a9",
            "base_tree": "1de79a7c48e6c66f167be54ca9cf387310149f80",
            "spark": {
                "audit_ids": ["RE-AUDIT-A", "RE-AUDIT-B", "RE-AUDIT-C"],
                "expected_raw_platform_sha256": {
                    "RE-AUDIT-A": "5d19dc0794088096df5030474fc1ae3e74e2ec850e18237ca6dd9cb399496079",
                    "RE-AUDIT-B": "26459969e27a38c3ce23eaa0e5a03d3dd3be7cc0fd70985fc015769408e9671a",
                    "RE-AUDIT-C": "df6da9eb3b530042da54ed8fce264bc44d5ee2d1c9dee80ccd5423ca9a6de1d7",
                },
                "normalized_finding_ids": {
                    "RE-AUDIT-A": ["A-1", "A-2"],
                    "RE-AUDIT-B": ["B-1", "B-2"],
                    "RE-AUDIT-C": ["C-1", "C-2"],
                },
                "author_closure_denominator": 6,
                "author_finding_ids": ["A-1", "A-2", "B-1", "B-2", "C-1", "C-2"],
                "historical_findings": {},
                "expected_historical": {},
                "expected_current": {},
            },
            "gate_stage_map": {
                "G-TARGETED": "targeted",
                "G-FULL": "full",
                "G-FRESH": "fresh",
            },
        }

    def test_default_unfrozen(self) -> None:
        """Default (file absent) milestone is not frozen."""
        self._patch_env(ZGOV_MILESTONE_PATH="/nonexistent/milestone.json")
        cfg = milestone.load_milestone()
        self.assertFalse(milestone.is_frozen(cfg))
        with self.assertRaises(milestone.MilestoneNotFrozen) as ctx:
            milestone.require_frozen(cfg)
        self.assertIn("MILESTONE_NOT_FROZEN", str(ctx.exception))

    def test_frozen_missing_base_sha(self) -> None:
        """frozen: true with a missing base_sha raises MilestoneError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "milestone.json"
            data = self._valid_frozen()
            data["base_sha"] = None
            path.write_text(json.dumps(data), encoding="utf-8")
            self._patch_env(ZGOV_MILESTONE_PATH=str(path))
            with self.assertRaises(milestone.MilestoneError) as ctx:
                milestone.load_milestone()
            self.assertIn("base_sha", str(ctx.exception))

    def test_frozen_audit_sha_key_mismatch(self) -> None:
        """frozen: true with inconsistent audit/sha keys raises MilestoneError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "milestone.json"
            data = self._valid_frozen()
            data["spark"]["expected_raw_platform_sha256"]["EXTRA"] = "0" * 64
            path.write_text(json.dumps(data), encoding="utf-8")
            self._patch_env(ZGOV_MILESTONE_PATH=str(path))
            with self.assertRaises(milestone.MilestoneError) as ctx:
                milestone.load_milestone()
            self.assertIn("audit_ids", str(ctx.exception))

    def test_frozen_all_consistent(self) -> None:
        """A fully consistent frozen milestone validates and exposes identity."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "milestone.json"
            data = self._valid_frozen()
            path.write_text(json.dumps(data), encoding="utf-8")
            self._patch_env(ZGOV_MILESTONE_PATH=str(path))
            cfg = milestone.load_milestone()
            self.assertTrue(milestone.is_frozen(cfg))
            self.assertEqual(
                milestone.base_identity(cfg),
                (
                    "e18439c8dfe01d901895efd09b8b73b6842327a9",
                    "1de79a7c48e6c66f167be54ca9cf387310149f80",
                ),
            )

    def test_frozen_int_rejected(self) -> None:
        """frozen: 1 (int) is rejected as a non-bool."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "milestone.json"
            data = self._valid_frozen()
            data["frozen"] = 1
            path.write_text(json.dumps(data), encoding="utf-8")
            self._patch_env(ZGOV_MILESTONE_PATH=str(path))
            with self.assertRaises(milestone.MilestoneError) as ctx:
                milestone.load_milestone()
            self.assertIn("frozen", str(ctx.exception))

    def test_gate_stage_map_not_injective(self) -> None:
        """Two gates mapping to the same stage raises MilestoneError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "milestone.json"
            data = self._valid_frozen()
            data["gate_stage_map"]["G-EXTRA"] = "full"
            path.write_text(json.dumps(data), encoding="utf-8")
            self._patch_env(ZGOV_MILESTONE_PATH=str(path))
            with self.assertRaises(milestone.MilestoneError) as ctx:
                milestone.load_milestone()
            self.assertIn("stage", str(ctx.exception).lower())

    def test_base_sha_too_short(self) -> None:
        """A 39-character base_sha raises MilestoneError."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "milestone.json"
            data = self._valid_frozen()
            data["base_sha"] = "e18439c8dfe01d901895efd09b8b73b6842327a"  # 39 chars
            path.write_text(json.dumps(data), encoding="utf-8")
            self._patch_env(ZGOV_MILESTONE_PATH=str(path))
            with self.assertRaises(milestone.MilestoneError) as ctx:
                milestone.load_milestone()
            self.assertIn("base_sha", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
