"""Regression coverage for binary ionic solid presentation defaults."""

from __future__ import annotations

from pathlib import Path

from mat_viewer.app import ViewerBackend
from mat_viewer.loader import build_bundle_scene
from mat_viewer.loader.bundle_builder import build_loaded_crystal_from_atoms
from mat_viewer.structure.cif_parse import load_cif
from mat_viewer.presets import default_preset_path
from mat_viewer.topology.analysis import _MAX_REGISTERED_CN
from mat_viewer.structure.bonds import (
    inorganic_solid_profile,
    ionic_coordinate_source_pairs,
)


def test_ionic_contact_filter_only_matches_binary_metal_halides():
    atoms = [{"elem": "Cs"}, {"elem": "Cl"}]
    records = [{"left": 0, "right": 1}]
    assert ionic_coordinate_source_pairs(atoms, records) == {(0, 1)}

    covalent = [{"elem": "C"}, {"elem": "Cl"}]
    assert ionic_coordinate_source_pairs(covalent, records) == set()


def test_extended_inorganic_profile_covers_oxide_lattices_without_mof_false_positive():
    rutile = [{"elem": "Ti"}, {"elem": "O"}]
    perovskite = [{"elem": "Ca"}, {"elem": "Ti"}, {"elem": "O"}]
    hkust = [{"elem": "Cu"}, {"elem": "C"}, {"elem": "O"}, {"elem": "N"}]

    assert inorganic_solid_profile(rutile)["is_inorganic_solid"] is True
    assert inorganic_solid_profile(perovskite)["is_inorganic_solid"] is True
    assert inorganic_solid_profile(hkust)["is_inorganic_solid"] is False


def test_nacl_keeps_mck_contact_but_hides_ionic_sticks():
    parsed = load_cif("benchmarks/structures/ionic_nacl.cif")
    bundle = build_loaded_crystal_from_atoms(
        name="ionic-nacl",
        source_path="benchmarks/structures/ionic_nacl.cif",
        raw_atoms=parsed.atoms,
        cell=parsed.cell,
        M=parsed.matrix,
        source="upload",
    )
    assert bundle.scene["is_ionic_solid"] is True
    assert bundle.scene["_canonical_bond_records"]
    assert bundle.scene["bonds"] == []

    unit_cell = build_bundle_scene(
        bundle,
        display_mode="unit_cell",
        show_hydrogen=True,
        preset={},
    )
    assert len(unit_cell["draw_atoms"]) == 16
    assert unit_cell["bonds"] == []
    assert unit_cell["suppressed_ionic_bond_count"] > 0


def test_binary_ionic_default_state_uses_cell_and_atom_coordination(tmp_path: Path):
    backend = ViewerBackend(
        preset_path=default_preset_path(),
        names=[],
        root_dir=str(tmp_path),
    )
    parsed = load_cif("benchmarks/structures/ionic_nacl.cif")
    bundle = build_loaded_crystal_from_atoms(
        name="ionic-nacl",
        source_path="benchmarks/structures/ionic_nacl.cif",
        raw_atoms=parsed.atoms,
        cell=parsed.cell,
        M=parsed.matrix,
        source="upload",
    )
    backend.bundles[bundle.name] = bundle
    backend.structure_names.append(bundle.name)
    try:
        state = backend.default_state(bundle.name)
    finally:
        backend.close()

    assert state["display_mode"] == "unit_cell"
    assert state["style"] == "ball"
    assert "unit_cell_box" in state["display_options"]
    assert state["topology_enabled"] is True
    assert state["polyhedron_specs"][0]["level"] == "atom"
    assert state["polyhedron_specs"][0]["center_species"] == "Na"
    assert state["polyhedron_specs"][0]["ligand_species"] == "Cl"
    assert state["polyhedron_specs"][0]["fallback_max"] == 12


def test_legacy_ionic_spec_gets_safe_coordination_cap(tmp_path: Path):
    backend = ViewerBackend(
        preset_path=default_preset_path(), names=[], root_dir=str(tmp_path)
    )
    state = backend.get_state()
    state["polyhedron_specs"] = [
        {
            "id": "ionic-coordination",
            "level": "atom",
            "center_species": "Mg",
            "ligand_species": "O",
            "fallback_max": None,
        }
    ]
    try:
        effective = backend._effective_polyhedron_specs(state)
    finally:
        backend.close()
    assert effective[0]["fallback_max"] == _MAX_REGISTERED_CN
