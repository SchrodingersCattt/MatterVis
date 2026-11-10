"""Regression coverage for binary ionic solid presentation defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.loader import build_bundle_scene, build_empty_bundle
from mat_viewer.loader.bundle_builder import build_loaded_crystal_from_atoms
from mat_viewer.presets import default_preset_path
from mat_viewer.structure.bonds import inorganic_solid_profile
from mat_viewer.structure.cif_parse import load_cif
from mat_viewer.topology.analysis import _MAX_REGISTERED_CN


@pytest.mark.parametrize(
    ("elements", "cation", "anion"),
    [
        (("Na", "Cl"), "Na", "Cl"),
        (("Cs", "Cl"), "Cs", "Cl"),
        (("Ca", "F"), "Ca", "F"),
        (("Ti", "O"), "Ti", "O"),
        (("Ca", "Ti", "O"), "Ca", "O"),
    ],
)
def test_representative_inorganic_defaults_stay_on_full_cell(
    tmp_path: Path,
    elements: tuple[str, ...],
    cation: str,
    anion: str,
):
    """The default state must not fall back to a formula-unit molecule.

    Keep this at the backend boundary so the guard covers both binary salts
    and covalent oxide lattices (rutile and perovskite), including scenes
    restored from a cache that lacks the newer profile metadata.
    """
    backend = ViewerBackend(
        preset_path=default_preset_path(), names=[], root_dir=str(tmp_path)
    )
    try:
        name = "representative-inorganic"
        bundle = build_empty_bundle(name=name)
        bundle.raw_atoms = [{"elem": element} for element in elements]
        bundle.scene.update(
            {
                # Deliberately omit ``is_inorganic_solid`` to exercise the
                # raw-atom fallback used for legacy/cache scenes.
                "inorganic_cation_elements": [],
                "inorganic_anion_elements": [],
                "fragment_table": [
                    {
                        "formula": cation,
                        "elem_set": [cation],
                        "heavy_atom_count": 1,
                    }
                ],
            }
        )
        backend.bundles[name] = bundle
        backend.structure_names.append(name)

        state = backend.default_state(name)

        assert state["display_mode"] == "unit_cell"
        assert state["style"] == "ball"
        assert "unit_cell_box" in state["display_options"]
        assert state["camera"] is None
        assert state["topology_enabled"] is True
        assert state["polyhedron_specs"][0]["center_species"] == cation
        assert state["polyhedron_specs"][0]["ligand_species"] == anion
    finally:
        backend.close()


def test_extended_inorganic_profile_covers_oxide_lattices_without_mof_false_positive():
    rutile = [{"elem": "Ti"}, {"elem": "O"}]
    perovskite = [{"elem": "Ca"}, {"elem": "Ti"}, {"elem": "O"}]
    hkust = [{"elem": "Cu"}, {"elem": "C"}, {"elem": "O"}, {"elem": "N"}]

    assert inorganic_solid_profile(rutile)["is_inorganic_solid"] is True
    assert inorganic_solid_profile(perovskite)["is_inorganic_solid"] is True
    assert inorganic_solid_profile(hkust)["is_inorganic_solid"] is False


def test_nacl_unit_cell_keeps_centres_inside_and_coordination_sticks():
    parsed = load_cif("benchmarks/structures/ionic_nacl.cif")
    bundle = build_loaded_crystal_from_atoms(
        name="ionic-nacl",
        source_path="benchmarks/structures/ionic_nacl.cif",
        raw_atoms=parsed.atoms,
        cell=parsed.cell,
        M=parsed.matrix,
        source="upload",
    )
    assert bundle.scene["connectivity_regime"] == "extended"
    assert bundle.scene["_canonical_bond_records"]
    assert {record["role"] for record in bundle.scene["_canonical_bond_records"]} == {
        "coordination"
    }

    unit_cell = build_bundle_scene(
        bundle,
        display_mode="unit_cell",
        show_hydrogen=True,
        preset={},
    )
    # Origin Na is a corner site (8 images) and Cl at (0.5, 0, 0) is an
    # edge site (4 images). Symmetric 0/1 copies stay inside the cell;
    # the old spanning-bond shell (4 atoms, including Cl at -0.5) does not.
    assert len(unit_cell["draw_atoms"]) == 12
    assert unit_cell["framework_context_replica_count"] == 0
    assert unit_cell["bonds"]
    assert {bond["role"] for bond in unit_cell["bonds"]} == {"coordination"}
    for atom in unit_cell["draw_atoms"]:
        frac = atom["frac"]
        assert all(-1e-8 <= float(component) <= 1.0 + 1e-8 for component in frac)


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
