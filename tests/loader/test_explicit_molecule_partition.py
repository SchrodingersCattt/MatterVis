"""Explicit molecular identities survive ASE/ExtXYZ scene construction."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from ase import Atoms
from ase.io import write

from mat_viewer.loader import build_bundle_scene, load_structure_input


def test_extxyz_keeps_explicit_interleaved_molecules(tmp_path: Path) -> None:
    # A metal ion near a water molecule; positions are interleaved on purpose.
    atoms = Atoms(
        ['O', 'Na', 'H', 'H'],
        positions=[[4.8, 5.0, 5.0], [4.8, 5.0, 7.3],
                   [5.76, 5.0, 5.0], [4.56, 5.93, 5.0]],
        cell=[10, 10, 10], pbc=True,
    )
    atoms.set_array('molecule_index', np.array([70, 5, 70, 70], dtype=int))
    path = tmp_path / 'partition.extxyz'
    write(path, atoms, format='extxyz')

    structure = load_structure_input(path)
    bundle = structure.frames[0].bundle
    groups = {frozenset(group) for group in bundle.molcrys_analysis.mol_indices}
    assert groups == {frozenset({1}), frozenset({0, 2, 3})}
    assert np.array_equal(bundle.atom_arrays['molecule_index'], [70, 5, 70, 70])
    assert np.allclose([atom['cart'] for atom in bundle.raw_atoms], atoms.positions)
    for bond in bundle.molcrys_analysis.bond_records:
        assert atoms.arrays['molecule_index'][bond['left']] == atoms.arrays['molecule_index'][bond['right']]
    scene = build_bundle_scene(bundle, display_mode='unit_cell', show_hydrogen=True,
                               include_boundary_replicas=True)
    for atom in scene['draw_atoms']:
        assert atom['_source_index'] in range(4)


def test_explicit_partition_still_obeys_custom_bond_thresholds(tmp_path: Path) -> None:
    atoms = Atoms('CC', positions=[[4, 5, 5], [5.6, 5, 5]], cell=[10]*3, pbc=True)
    atoms.set_array('molecule_index', np.array([12, 12], dtype=int))
    path = tmp_path / 'threshold.extxyz'
    write(path, atoms, format='extxyz')

    narrow = load_structure_input(path, bond_thresholds={('C', 'C'): 1.2})
    wide = load_structure_input(path, bond_thresholds={('C', 'C'): 2.0})
    assert len(narrow.frames[0].bundle.molcrys_analysis.bond_records) == 0
    assert len(wide.frames[0].bundle.molcrys_analysis.bond_records) == 1


@pytest.mark.parametrize('ids', [np.array([-1, 0]), np.array([0.0, 1.0])])
def test_invalid_molecule_partition_is_rejected(tmp_path: Path, ids) -> None:
    atoms = Atoms('CC', positions=[[2, 2, 2], [3, 2, 2]], cell=[10]*3, pbc=True)
    atoms.set_array('molecule_index', ids)
    path = tmp_path / 'invalid.extxyz'
    write(path, atoms, format='extxyz')
    with pytest.raises(ValueError, match='molecule_index'):
        load_structure_input(path)


def test_molecule_partition_without_lattice_uses_synthetic_cell(tmp_path: Path) -> None:
    atoms = Atoms('CC', positions=[[0, 0, 0], [1.4, 0, 0]])
    atoms.set_array('molecule_index', np.array([8, 8], dtype=int))
    path = tmp_path / 'molecule.extxyz'
    write(path, atoms, format='extxyz')
    loaded = load_structure_input(path)
    assert len(loaded.frames[0].bundle.molcrys_analysis.mol_indices) == 1
    assert len(loaded.frames[0].bundle.molcrys_analysis.bond_records) == 1
