from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mat_viewer.structure import molcrys_bridge


def _site(global_index: int, local_index: int, position):
    return SimpleNamespace(
        global_index=global_index,
        molecule_index=0,
        local_index=local_index,
        cartesian_position_A=tuple(position),
    )


def test_analysis_consumes_only_public_molcryskit_records(monkeypatch):
    sites = (_site(0, 0, (0.0, 0.0, 0.0)), _site(1, 1, (1.0, 0.0, 0.0)))
    bonds = (
        SimpleNamespace(
            molecule_index=0,
            left_local_index=0,
            right_local_index=1,
            left_global_index=0,
            right_global_index=1,
            left_asym_index=3,
            right_asym_index=4,
            right_image_shift=(1, 0, 0),
            vector_A=(1.0, 0.0, 0.0),
            distance_A=1.0,
        ),
    )
    selection = SimpleNamespace(
        members=(
            SimpleNamespace(species_id="C2_1", molecule_index=0, image_shift=(0, 1, 0)),
        )
    )

    class Analyzer:
        def __init__(self, crystal):
            self.species_map = {"C2_1": [0]}

        def get_simplest_unit(self):
            return {"C2_1": 1}

        def select_formula_unit(self):
            return selection

    ring = SimpleNamespace(
        atom_indices=(0, 1),
        cycle_atom_indices=(1, 0),
        symbols=("C", "C"),
        centroid_A=(0.5, 0.0, 0.0),
        normal=(0.0, 0.0, 1.0),
        plane_rmsd_A=0.0,
        is_planar=True,
        is_aromatic=True,
        size=2,
    )

    class GeometryCache:
        def __init__(self, crystal):
            pass

        def __getitem__(self, molecule_index):
            return SimpleNamespace(rings=lambda: [ring])

    crystal = SimpleNamespace(
        molecules=[object()],
        get_site_records=lambda: list(sites),
        get_bond_records=lambda: list(bonds),
    )
    monkeypatch.setattr(
        molcrys_bridge,
        "_require_molcryskit",
        lambda: {
            "StoichiometryAnalyzer": Analyzer,
            "LocalGeometryCache": GeometryCache,
        },
    )

    analysis = molcrys_bridge.analyze_crystal(crystal)

    assert analysis.mol_indices == [[0, 1]]
    assert analysis.bond_pairs == [(0, 1)]
    assert analysis.bond_records[0]["right_image_shift"] == [1, 0, 0]
    assert analysis.formula_unit_selection is selection
    assert analysis.ring_records[0]["atom_indices"] == (0, 1)
    assert analysis.ring_records[0]["cycle_atom_indices"] == (1, 0)
    assert analysis.ring_records[0]["is_aromatic"] is True


def test_formula_unit_materialises_mck_image_shift():
    raw_atoms = [
        {"elem": "C", "cart": np.array([0.0, 0.0, 0.0]), "frac": np.zeros(3)},
        {
            "elem": "C",
            "cart": np.array([1.0, 0.0, 0.0]),
            "frac": np.array([0.1, 0.0, 0.0]),
        },
    ]
    selection = SimpleNamespace(
        members=(
            SimpleNamespace(species_id="C2_1", molecule_index=0, image_shift=(0, 1, 0)),
        )
    )
    analysis = SimpleNamespace(
        formula_unit_selection=selection,
        mol_indices=[[0, 1]],
        mol_cart_positions=[np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])],
    )

    atoms = molcrys_bridge.select_formula_unit(
        raw_atoms, np.diag([10.0, 20.0, 30.0]), analysis=analysis
    )

    assert np.asarray(atoms[0]["cart"]) == pytest.approx([0.0, 20.0, 0.0])
    assert np.asarray(atoms[1]["cart"]) == pytest.approx([1.0, 20.0, 0.0])
    assert [atom["_source_index"] for atom in atoms] == [0, 1]
    assert all(atom["_formula_image_shift"] == [0, 1, 0] for atom in atoms)
