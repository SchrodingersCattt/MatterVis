from __future__ import annotations

from pathlib import Path

import numpy as np

from crystal_viewer.structure.extxyz import parse_extxyz
from crystal_viewer.structure import molcrys_bridge


ROOT = Path(__file__).resolve().parents[2]
EXTXYZ = ROOT / "_tmp" / "halogen_bond.extxyz"


def test_parse_extxyz_halogen_bond_fixture() -> None:
    result = parse_extxyz(str(EXTXYZ))

    assert result.source_frame_index == 0
    assert result.source_frame_count == 20
    assert len(result.raw_atoms) == 248
    assert {atom["elem"] for atom in result.raw_atoms} == {"H", "C", "Br", "N", "O"}
    assert np.asarray(result.M).shape == (3, 3)
    assert abs(np.linalg.det(np.asarray(result.M, dtype=float))) > 1e-6

    labels = [atom["label"] for atom in result.raw_atoms]
    assert len(labels) == len(set(labels))
    for idx, atom in enumerate(result.raw_atoms):
        assert atom["_source_index"] == idx
        assert "_extxyz_molecule_index" in atom
        assert np.allclose(np.asarray(atom["frac"], dtype=float) @ result.M, atom["cart"])


def test_analyze_from_extxyz_crystal_aligns_indices() -> None:
    result = parse_extxyz(str(EXTXYZ))
    analysis = molcrys_bridge.analyze_from_crystal(result.crystal, raw_atoms=result.raw_atoms)

    assert len(analysis.mol_indices) == 8
    flat = sorted(idx for group in analysis.mol_indices for idx in group)
    assert flat == list(range(len(result.raw_atoms)))
    assert all(
        np.asarray(coords).shape[0] == len(indices)
        for coords, indices in zip(analysis.mol_cart_positions, analysis.mol_indices)
    )
    assert analysis.species_map
    assert analysis.per_fu


def test_parse_extxyz_can_select_frame() -> None:
    result = parse_extxyz(str(EXTXYZ), frame_index=1)
    assert result.source_frame_index == 1
    assert result.source_frame_count == 20
    assert len(result.raw_atoms) > 0
