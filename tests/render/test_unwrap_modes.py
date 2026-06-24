from __future__ import annotations

import gemmi
import numpy as np

from crystal_viewer.structure import molcrys_bridge
from crystal_viewer.loader import _unwrapped_atoms_from_atoms
from crystal_viewer.scene import build_scene_from_atoms, scene_ops


def test_unit_cell_prefers_unwrapped_atoms_for_boundary_fragment():
    cell = gemmi.UnitCell(10.0, 10.0, 10.0, 90.0, 90.0, 90.0)
    M = np.eye(3) * 10.0
    atoms = [
        {
            "label": "C1",
            "elem": "C",
            "frac": np.array([0.98, 0.5, 0.5]),
            "cart": np.array([9.8, 5.0, 5.0]),
            "occ": 1.0,
            "dg": ".",
            "da": ".",
        },
        {
            "label": "C2",
            "elem": "C",
            "frac": np.array([0.02, 0.5, 0.5]),
            "cart": np.array([0.2, 5.0, 5.0]),
            "occ": 1.0,
            "dg": ".",
            "da": ".",
        },
    ]
    ops = scene_ops()
    # The legacy fallback that used to re-derive bonds via
    # ``ops.find_bonds(cell=cell)`` is gone; every caller (including
    # synthetic-atom test fixtures) must hand a real
    # ``CrystalAnalysis`` to ``_unwrapped_atoms_from_atoms``. We build
    # one from these two carbons by running the full bridge.
    analysis = molcrys_bridge.analyze(atoms, M)
    unwrapped_atoms, overflow = _unwrapped_atoms_from_atoms(
        atoms, cell, M, molcrys_analysis=analysis
    )

    assert overflow == []
    np.testing.assert_allclose(unwrapped_atoms[1]["cart"], [10.2, 5.0, 5.0])

    scene = build_scene_from_atoms(
        name="boundary",
        title="Boundary",
        atoms=atoms,
        cell=cell,
        M=M,
        R=np.eye(3),
        display_mode="unit_cell",
        ops=ops,
        unwrapped_atoms=unwrapped_atoms,
        preset={"style": {"show_labels": False, "show_axes": False}},
    )

    assert scene["draw_atoms"][1]["_unwrapped"] is True
    assert len(scene["bonds"]) == 1
    np.testing.assert_allclose(scene["bonds"][0]["end"], [10.2, 5.0, 5.0])
