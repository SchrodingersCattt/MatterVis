"""Explicit molecular identities survive ASE/ExtXYZ scene construction."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from ase import Atoms
from ase.io import write

from mat_viewer.loader import build_bundle_scene, load_structure_input
from mat_viewer.structure.source_partitions import identify_source_partitions


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


def _interleaved_frame() -> tuple[Atoms, np.ndarray]:
    """Two dimers interleaved as atoms 0,2 and 1,3, each closing through PBC."""
    atoms = Atoms(
        ['C', 'O', 'C', 'H'],
        positions=[
            [0.2, 5.0, 5.0],
            [5.0, 0.3, 5.0],
            [8.8, 5.0, 5.0],
            [5.0, 9.32, 5.0],
        ],
        cell=[10.0, 10.0, 10.0],
        pbc=True,
    )
    return atoms, np.array([0, 1, 0, 1], dtype=int)


def _local_identify(sliced, **_kwargs):
    """Stand-in for MolCrysKit: indices are local to the sliced partition."""
    assert len(sliced) == 2
    return [SimpleNamespace(info={
        'atom_indices': [0, 1],
        'bond_pairs': [(0, 1)],
        'bond_records': [{
            'left': 0,
            'right': 1,
            'right_image_shift': [1, 0, -1],
            'vector': [0.2, 0.0, -1.4],
            'role': 'covalent',
        }],
    })]


def _provenance(molecules) -> list[dict]:
    return [
        {
            'atom_indices': [int(index) for index in molecule.info['atom_indices']],
            'bond_pairs': [tuple(map(int, pair)) for pair in molecule.info['bond_pairs']],
            'bond_records': [
                {
                    'left': int(record['left']),
                    'right': int(record['right']),
                    'right_image_shift': [int(value) for value in record['right_image_shift']],
                    'vector': list(record['vector']),
                    'role': record['role'],
                }
                for record in molecule.info['bond_records']
            ],
        }
        for molecule in molecules
    ]


def test_noncontiguous_source_indices_stay_on_the_original_frame() -> None:
    atoms, molecule_indices = _interleaved_frame()
    molecules = identify_source_partitions(
        atoms, _local_identify, {}, molecule_indices,
    )
    assert [molecule.info['atom_indices'] for molecule in molecules] == [[0, 2], [1, 3]]
    for molecule in molecules:
        assert molecule.info['atom_indices'] != [0, 1]


def test_periodic_bond_shift_survives_index_rebase() -> None:
    atoms, molecule_indices = _interleaved_frame()
    molecules = identify_source_partitions(
        atoms, _local_identify, {}, molecule_indices,
    )
    expected = {
        (0, 2): {
            'left': 0,
            'right': 2,
            'right_image_shift': [1, 0, -1],
            'vector': [0.2, 0.0, -1.4],
            'role': 'covalent',
        },
        (1, 3): {
            'left': 1,
            'right': 3,
            'right_image_shift': [1, 0, -1],
            'vector': [0.2, 0.0, -1.4],
            'role': 'covalent',
        },
    }
    found = {}
    for molecule in molecules:
        record = molecule.info['bond_records'][0]
        pair = (record['left'], record['right'])
        found[pair] = record
        assert pair == tuple(molecule.info['bond_pairs'][0])
        assert pair != (0, 1)
    assert found == expected


def test_partition_round_trip_is_stable_and_does_not_cross_groups() -> None:
    atoms, molecule_indices = _interleaved_frame()
    atoms.set_array('molecule_index', molecule_indices.copy())
    positions = atoms.positions.copy()
    first = identify_source_partitions(atoms, _local_identify, {}, molecule_indices)
    second = identify_source_partitions(atoms, _local_identify, {}, molecule_indices)
    provenance = _provenance(first)
    assert provenance == _provenance(second)
    assert np.array_equal(atoms.arrays['molecule_index'], molecule_indices)
    assert np.allclose(atoms.positions, positions)

    recovered: dict[int, list[int]] = {}
    for molecule in provenance:
        member_ids = {int(molecule_indices[index]) for index in molecule['atom_indices']}
        assert len(member_ids) == 1
        source_id = member_ids.pop()
        recovered.setdefault(source_id, []).extend(molecule['atom_indices'])
        for left, right in molecule['bond_pairs']:
            assert int(molecule_indices[left]) == int(molecule_indices[right]) == source_id
        for record in molecule['bond_records']:
            assert int(molecule_indices[record['left']]) == source_id
            assert int(molecule_indices[record['right']]) == source_id
            assert record['right_image_shift'] == [1, 0, -1]
    assert recovered == {0: [0, 2], 1: [1, 3]}


def test_real_identifier_rebases_periodic_bonds_to_global_indices() -> None:
    from molcrys_kit.io.cif import identify_molecules

    atoms, molecule_indices = _interleaved_frame()
    kwargs = {
        'max_atoms': None,
        'bond_scale': 1.0,
        'bond_thresholds': None,
        'disorder_policy': 'as_refined',
    }
    first = identify_source_partitions(
        atoms, identify_molecules, kwargs, molecule_indices,
    )
    second = identify_source_partitions(
        atoms, identify_molecules, kwargs, molecule_indices,
    )

    def _global_bonds(molecules):
        bonds = []
        for molecule in molecules:
            atoms_in_molecule = [int(index) for index in molecule.info['atom_indices']]
            for record in molecule.info['bond_records']:
                left, right = int(record['left']), int(record['right'])
                bonds.append((
                    tuple(atoms_in_molecule),
                    left,
                    right,
                    tuple(int(value) for value in record['right_image_shift']),
                ))
        return bonds

    rebased = _global_bonds(first)
    assert rebased == _global_bonds(second)
    assert {atoms_in_molecule for atoms_in_molecule, *_rest in rebased} == {(0, 2), (1, 3)}
    assert any(shift != (0, 0, 0) for *_indices, shift in rebased)

    expected = {}
    for source_indices in ([0, 2], [1, 3]):
        for molecule in identify_molecules(atoms[source_indices], **kwargs):
            for record in molecule.info['bond_records']:
                left = source_indices[int(record['left'])]
                right = source_indices[int(record['right'])]
                expected[(left, right)] = tuple(
                    int(value) for value in record['right_image_shift']
                )
    assert expected
    for atoms_in_molecule, left, right, shift in rebased:
        assert left in atoms_in_molecule and right in atoms_in_molecule
        assert molecule_indices[left] == molecule_indices[right]
        assert {left, right} != {0, 1}
        assert shift == expected[(left, right)]


def _molecule(local_indices, records):
    return SimpleNamespace(
        info={
            'atom_indices': list(local_indices),
            'bond_pairs': [(int(record['left']), int(record['right'])) for record in records],
            'bond_records': records,
        }
    )


def test_one_partition_keeps_every_component_and_reordered_sites() -> None:
    atoms, _molecule_indices = _interleaved_frame()
    indices = np.array([4, 4, 4, 9], dtype=int)

    def identify(subset, **_kwargs):
        count = len(subset)
        assert count >= 1
        if count == 1:
            return [_molecule([0], [])]
        # Local index 1 is listed before 0, and the slice splits in two.
        return [
            _molecule(
                [1, 0],
                [{
                    'left': 1,
                    'right': 0,
                    'right_image_shift': [0, 1, 0],
                    'role': 'covalent',
                    'note': 'kept',
                }],
            ),
            _molecule([2], []),
        ]

    molecules = identify_source_partitions(atoms, identify, {}, indices)
    assert [molecule.info['atom_indices'] for molecule in molecules] == [[1, 0], [2], [3]]
    record = molecules[0].info['bond_records'][0]
    assert (record['left'], record['right']) == (1, 0)
    assert record['right_image_shift'] == [0, 1, 0]
    assert record['note'] == 'kept'
    assert molecules[0].info['bond_pairs'] == [(1, 0)]


def test_upstream_partition_argument_is_forwarded_without_a_second_remap() -> None:
    atoms, molecule_indices = _interleaved_frame()
    seen = {}

    def identify(frame, molecule_indices=None, **_kwargs):
        seen['length'] = len(frame)
        seen['indices'] = np.asarray(molecule_indices).copy()
        return [_molecule([0, 2], [{
            'left': 0,
            'right': 2,
            'right_image_shift': [1, 0, 0],
        }])]

    molecules = identify_source_partitions(atoms, identify, {'bond_scale': 1.0}, molecule_indices)
    assert seen['length'] == len(atoms)
    assert np.array_equal(seen['indices'], molecule_indices)
    assert molecules[0].info['atom_indices'] == [0, 2]
    assert molecules[0].info['bond_records'][0]['left'] == 0
