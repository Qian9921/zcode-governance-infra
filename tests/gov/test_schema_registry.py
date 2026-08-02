"""Consistency between ``zgov.contracts.SCHEMA_REGISTRY`` and the JSON registry.

The Python inventory and ``contracts/schema_registry.v16.json`` are two views of
one contract surface.  If they drift, a schema can be validated by code while
being absent from the published registry (or vice versa), so the drift is
locked down here.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import unittest

from zgov import contracts

_REGISTRY_JSON = (
    pathlib.Path(__file__).resolve().parents[2] / "gov" / "zgov" / "contracts" / "schema_registry.v16.json"
)

_VALID_MODES = frozenset({"standalone", "source-bound", "caller-bound"})

# Modules referenced by the registry that are not part of this repository yet.
# They are pinned so that porting one (or losing another) fails this test
# instead of silently widening the unverified surface.
_UNPORTED_MODULES = frozenset()


def _load_json_registry() -> dict:
    return json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))


class SchemaRegistryParity(unittest.TestCase):
    """The Python dict and the JSON file must describe the same schema set."""

    def setUp(self) -> None:
        self.py = contracts.SCHEMA_REGISTRY
        self.js = _load_json_registry()["schemas"]

    def test_key_sets_are_identical(self) -> None:
        py_keys, js_keys = set(self.py), set(self.js)
        self.assertEqual(
            py_keys,
            js_keys,
            "registry drift: "
            f"python={len(py_keys)} json={len(js_keys)} "
            f"python_only={sorted(py_keys - js_keys)} json_only={sorted(js_keys - py_keys)}",
        )

    def test_entries_are_field_for_field_equal(self) -> None:
        for schema_id in sorted(set(self.py) & set(self.js)):
            with self.subTest(schema_id=schema_id):
                self.assertEqual(self.py[schema_id], self.js[schema_id])


class SchemaRegistryShape(unittest.TestCase):
    """Per-entry structural invariants."""

    def setUp(self) -> None:
        self.py = contracts.SCHEMA_REGISTRY

    def test_required_and_optional_do_not_overlap(self) -> None:
        for schema_id, entry in sorted(self.py.items()):
            with self.subTest(schema_id=schema_id):
                overlap = set(entry.get("required", [])) & set(entry.get("optional", []))
                self.assertEqual(
                    overlap, set(), f"{schema_id}: fields in both required and optional: {sorted(overlap)}"
                )

    def test_validation_mode_is_one_of_three(self) -> None:
        counts = {mode: 0 for mode in _VALID_MODES}
        for schema_id, entry in sorted(self.py.items()):
            with self.subTest(schema_id=schema_id):
                mode = entry.get("validation_mode")
                self.assertIn(mode, _VALID_MODES, f"{schema_id}: invalid validation_mode {mode!r}")
            if mode in counts:
                counts[mode] += 1
        self.assertEqual(
            sum(counts.values()), len(self.py), f"unclassified entries; counts={counts}"
        )

    def test_every_entry_has_a_dotted_validator(self) -> None:
        for schema_id, entry in sorted(self.py.items()):
            with self.subTest(schema_id=schema_id):
                validator = entry.get("validator")
                self.assertIsInstance(validator, str)
                self.assertEqual(
                    validator.count("."), 1, f"{schema_id}: expected '<module>.<function>', got {validator!r}"
                )
                self.assertFalse(
                    validator.startswith("codex"), f"{schema_id}: stale codex reference {validator!r}"
                )


class SchemaRegistryValidatorsResolve(unittest.TestCase):
    """Each ``validator`` must name a real importable ``zgov`` callable."""

    def setUp(self) -> None:
        self.py = contracts.SCHEMA_REGISTRY

    def test_validators_of_ported_modules_are_importable(self) -> None:
        unresolvable: list[str] = []
        for schema_id, entry in sorted(self.py.items()):
            module_name, func_name = entry["validator"].rsplit(".", 1)
            if module_name in _UNPORTED_MODULES:
                continue
            try:
                module = importlib.import_module(f"zgov.{module_name}")
            except Exception as exc:  # noqa: BLE001 - reported verbatim below
                unresolvable.append(f"{schema_id}: zgov.{module_name} -> {type(exc).__name__}: {exc}")
                continue
            attr = getattr(module, func_name, None)
            if attr is None:
                unresolvable.append(f"{schema_id}: zgov.{module_name}.{func_name} -> attribute missing")
            elif not callable(attr):
                unresolvable.append(f"{schema_id}: zgov.{module_name}.{func_name} -> not callable")
        self.assertEqual(unresolvable, [], "unresolvable validators:\n  " + "\n  ".join(unresolvable))

    def test_unported_module_set_is_exactly_as_pinned(self) -> None:
        """Fail if an unported module gets ported, or a new one goes missing."""
        actually_missing: set[str] = set()
        for entry in self.py.values():
            module_name = entry["validator"].rsplit(".", 1)[0]
            try:
                importlib.import_module(f"zgov.{module_name}")
            except ModuleNotFoundError:
                actually_missing.add(module_name)
        self.assertEqual(
            actually_missing,
            set(_UNPORTED_MODULES),
            "update _UNPORTED_MODULES: "
            f"newly_missing={sorted(actually_missing - _UNPORTED_MODULES)} "
            f"now_ported={sorted(_UNPORTED_MODULES - actually_missing)}",
        )


if __name__ == "__main__":
    unittest.main()
