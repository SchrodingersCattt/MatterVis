"""Phase 1 polyhedron-specs data model -- backend layer.

These tests cover the named-row ``polyhedron_specs`` model that
replaced (alongside) the legacy ``topology_species_keys`` selector:

  * ``_normalize_polyhedron_spec`` / ``_normalize_polyhedron_specs``
    coerce arbitrary user payloads into the canonical shape (id, name,
    center_species, ligand_species, color, enabled), reject malformed
    rows, and assign auto colours from the cycling palette.
  * ``ViewerBackend.add_polyhedron_spec`` / ``update_polyhedron_spec`` /
    ``remove_polyhedron_spec`` / ``reorder_polyhedron_specs`` operate on
    the active scene's state, persist via ``patch_state``, and survive a
    round-trip through the scene store.
  * ``_effective_polyhedron_specs`` only returns explicit named rows.
    MatterVis no longer synthesises auto-ligand packing shells; those
    semantics live in MolCrysKit's molecule-level ``find_polyhedra``.

DO NOT REMOVE -- this guards the contract documented in
``agents/polyhedron_api.md``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import (
    DEFAULT_CENTROID_OFFSET_FRAC,
    ViewerBackend,
    _normalize_polyhedron_spec,
    _normalize_polyhedron_specs,
)
from crystal_viewer import topology as topology_module
from crystal_viewer.presets import default_preset_path


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))


# ---- normaliser unit tests ------------------------------------------------


def test_normalize_polyhedron_spec_assigns_id_and_lowercases_color():
    existing: set[str] = set()
    spec = _normalize_polyhedron_spec(
        {"center_species": "ClO4", "color": "#ABCDEF"},
        fallback_color="#7C5CBF",
        existing_ids=existing,
    )
    assert spec is not None
    assert spec["center_species"] == "ClO4"
    assert spec["ligand_species"] is None
    assert spec["color"] == "#abcdef"
    assert spec["enabled"] is True
    assert spec["name"] == "ClO4"
    assert spec["id"] in existing
    assert spec["enforce_enclosure"] is True
    assert spec["centroid_offset_frac"] == DEFAULT_CENTROID_OFFSET_FRAC


def test_normalize_polyhedron_spec_accepts_packing_shell_knobs():
    spec = _normalize_polyhedron_spec(
        {
            "center_species": "C2N2",
            "ligand_species": "ClO4",
            "enforce_enclosure": False,
            "centroid_offset_frac": "0.65",
        },
        fallback_color="#7C5CBF",
        existing_ids=set(),
    )

    assert spec is not None
    assert spec["enforce_enclosure"] is False
    assert spec["centroid_offset_frac"] == 0.65


def test_normalize_polyhedron_spec_rejects_invalid_color_to_fallback():
    spec = _normalize_polyhedron_spec(
        {"center_species": "Cl", "color": "rgba(255,0,0,0.5)"},
        fallback_color="#123456",
        existing_ids=set(),
    )
    assert spec is not None
    assert spec["color"] == "#123456"


def test_normalize_polyhedron_spec_drops_rows_without_center():
    assert (
        _normalize_polyhedron_spec(
            {"name": "no centre"}, fallback_color="#000000", existing_ids=set()
        )
        is None
    )
    assert (
        _normalize_polyhedron_spec(
            "not a dict", fallback_color="#000000", existing_ids=set()
        )
        is None
    )


def test_normalize_polyhedron_specs_uses_palette_for_missing_colors():
    specs = _normalize_polyhedron_specs(
        [
            {"center_species": "A"},
            {"center_species": "B"},
            {"center_species": "C"},
        ]
    )
    assert len(specs) == 3
    colors = {spec["color"] for spec in specs}
    assert len(colors) == 3, "palette must yield distinct colours for distinct rows"


def test_normalize_polyhedron_specs_replaces_duplicate_ids():
    specs = _normalize_polyhedron_specs(
        [
            {"id": "dup", "center_species": "A"},
            {"id": "dup", "center_species": "B"},
        ]
    )
    assert len(specs) == 2
    assert specs[0]["id"] == "dup"
    assert specs[1]["id"] != "dup", "duplicate id must be regenerated"


# ---- backend CRUD ---------------------------------------------------------


def test_add_polyhedron_spec_persists_to_active_scene(backend: ViewerBackend):
    spec = backend.add_polyhedron_spec(
        center_species="ClO4",
        ligand_species=None,
        name="ClO4 anion cage",
        color="#FF6A00",
    )
    assert spec["center_species"] == "ClO4"
    assert spec["color"] == "#ff6a00"

    state = backend.get_state()
    assert any(item["id"] == spec["id"] for item in state["polyhedron_specs"])


def test_polyhedra_overlay_defaults_off(backend: ViewerBackend):
    state = backend.get_state()

    assert state["topology_enabled"] is False
    assert backend.topology_for_state(state) is None


def test_add_polyhedron_spec_rejects_missing_center(backend: ViewerBackend):
    with pytest.raises(ValueError):
        backend.add_polyhedron_spec(center_species="")


def test_update_polyhedron_spec_keeps_id_and_overrides_fields(backend: ViewerBackend):
    spec = backend.add_polyhedron_spec(center_species="ClO4", color="#FF0000")
    updated = backend.update_polyhedron_spec(
        spec["id"],
        {
            "color": "#00FF00",
            "name": "renamed",
            "enabled": False,
            "enforce_enclosure": False,
            "centroid_offset_frac": 0.5,
        },
    )
    assert updated["id"] == spec["id"]
    assert updated["color"] == "#00ff00"
    assert updated["name"] == "renamed"
    assert updated["enabled"] is False
    assert updated["enforce_enclosure"] is False
    assert updated["centroid_offset_frac"] == 0.5


def test_update_polyhedron_spec_unknown_id_raises(backend: ViewerBackend):
    with pytest.raises(KeyError):
        backend.update_polyhedron_spec("does_not_exist", {"color": "#000000"})


def test_remove_polyhedron_spec_returns_false_for_unknown_id(backend: ViewerBackend):
    assert backend.remove_polyhedron_spec("nope") is False


def test_remove_polyhedron_spec_drops_row(backend: ViewerBackend):
    spec = backend.add_polyhedron_spec(center_species="ClO4")
    assert backend.remove_polyhedron_spec(spec["id"]) is True
    assert all(item["id"] != spec["id"] for item in backend.list_polyhedron_specs())


def test_reorder_polyhedron_specs_requires_full_set(backend: ViewerBackend):
    a = backend.add_polyhedron_spec(center_species="A")
    b = backend.add_polyhedron_spec(center_species="B")
    backend.reorder_polyhedron_specs([b["id"], a["id"]])
    specs = backend.list_polyhedron_specs()
    assert [item["id"] for item in specs] == [b["id"], a["id"]]
    with pytest.raises(ValueError):
        backend.reorder_polyhedron_specs([a["id"]])  # missing one
    with pytest.raises(ValueError):
        backend.reorder_polyhedron_specs([a["id"], b["id"], "extra"])


# ---- explicit specs only --------------------------------------------------


def test_effective_specs_do_not_synthesize_auto_ligand_rows(backend: ViewerBackend):
    state = backend.get_state()
    state["polyhedron_specs"] = []
    state["topology_species_keys"] = ["NH4", "C6N2"]
    state["topology_hull_color"] = "#7C5CBF"

    effective = backend._effective_polyhedron_specs(state)
    assert effective == []


def test_explicit_polyhedron_specs_override_legacy_fields(backend: ViewerBackend):
    state = backend.get_state()
    state["polyhedron_specs"] = [
        {
            "id": "spec_a",
            "name": "custom",
            "center_species": "ClO4",
            "ligand_species": None,
            "color": "#aabbcc",
            "enabled": True,
        }
    ]
    state["topology_species_keys"] = ["legacy_ignored"]

    effective = backend._effective_polyhedron_specs(state)
    assert len(effective) == 1
    assert effective[0]["center_species"] == "ClO4"
    assert effective[0]["color"] == "#aabbcc"


def test_disabled_specs_drop_from_effective_list(backend: ViewerBackend):
    state = backend.get_state()
    state["polyhedron_specs"] = [
        {"id": "a", "center_species": "X", "color": "#000000", "enabled": False, "name": "x"},
        {"id": "b", "center_species": "Y", "color": "#000000", "enabled": True, "name": "y"},
    ]
    effective = backend._effective_polyhedron_specs(state)
    assert [spec["center_species"] for spec in effective] == ["Y"]


def test_mck_polyhedron_record_passes_packing_shell_knobs(monkeypatch):
    captured = {}

    def fake_find_polyhedra(*args, **kwargs):
        captured["kwargs"] = kwargs
        return [{"center_position": [0.0, 0.0, 0.0], "shell_coords": [], "shell_distances": []}]

    monkeypatch.setattr(topology_module.molcrys_bridge, "molecular_crystal_from_bundle", lambda bundle: object())
    monkeypatch.setattr(topology_module, "find_polyhedra", fake_find_polyhedra)

    record = topology_module._mck_polyhedron_record(
        object(),
        {
            "index": 0,
            "center": [0.0, 0.0, 0.0],
            "formula": "C2N2",
            "source_molecule_index": 4,
        },
        10.0,
        ligand_species=("ClO4",),
        enforce_enclosure=False,
        centroid_offset_frac=0.7,
    )

    assert record is not None
    assert captured["kwargs"]["enforce_enclosure"] is False
    assert captured["kwargs"]["centroid_offset_frac"] == 0.7
