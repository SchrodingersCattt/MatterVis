"""Deterministic crystals for the Display / Operate / Analysis fixtures.

F1 is an ordered nonorthogonal cell. F2 carries explicit hydrogens.
F3 is a geometric CN=6 model. F4 carries two occupancy alternatives.
F5 is the insertion target. F6 is a two-frame property pair.
"""

from __future__ import annotations

import numpy as np
from ase import Atoms
from molcrys_kit.constants.config import KEY_ASSEMBLY, KEY_DISORDER_GROUP, KEY_LABEL, KEY_OCCUPANCY
from molcrys_kit.structures.crystal import MolecularCrystal

F1_LATTICE = np.array([[7.0, 0.0, 0.0], [1.1, 8.0, 0.0], [0.4, 0.7, 9.0]], dtype=float)


def _crystal(lattice, symbols, positions, *, pbc=(True, True, True)) -> MolecularCrystal:
    atoms = Atoms(symbols=symbols, positions=np.asarray(positions, dtype=float), cell=np.asarray(lattice, dtype=float), pbc=list(pbc))
    return MolecularCrystal(np.asarray(lattice, dtype=float), [atoms], pbc=tuple(pbc))


def f1_ordered_nonorthogonal() -> MolecularCrystal:
    return _crystal(F1_LATTICE, ["C", "O"], [[2.0, 2.0, 2.0], [3.2, 2.1, 2.05]])


def f2_hydrogen_pbc() -> MolecularCrystal:
    lattice = np.array([[6.0, 0.0, 0.0], [0.4, 6.2, 0.0], [0.2, 0.3, 6.5]], dtype=float)
    # A water molecule sits against the a-face so a periodic image is meaningful.
    return _crystal(lattice, ["O", "H", "H"], [[0.3, 2.0, 2.0], [1.1, 2.2, 2.1], [5.7, 2.4, 1.9]])


def f3_octahedron() -> MolecularCrystal:
    lattice = np.eye(3) * 20.0
    center = np.array([10.0, 10.0, 10.0])
    positions = [center]
    for axis in range(3):
        for sign in (-2.0, 2.0):
            point = center.copy()
            point[axis] += sign
            positions.append(point)
    return _crystal(lattice, ["Ni", "O", "O", "O", "O", "O", "O"], positions)


def f4_disorder() -> MolecularCrystal:
    lattice = np.eye(3) * 10.0
    carbon = Atoms("C", positions=[[5.0, 5.0, 5.0]], cell=lattice, pbc=True)
    first = Atoms("O", positions=[[2.0, 2.0, 2.0]], cell=lattice, pbc=True)
    second = Atoms("O", positions=[[3.0, 2.0, 2.0]], cell=lattice, pbc=True)
    for molecule, group, label in ((first, 1, "O1"), (second, 2, "O1B")):
        molecule.set_array(KEY_OCCUPANCY, np.array([0.5]))
        molecule.set_array(KEY_DISORDER_GROUP, np.array([group]))
        molecule.set_array(KEY_ASSEMBLY, np.array(["A"]))
        molecule.set_array(KEY_LABEL, np.array([label]))
    return MolecularCrystal(lattice, [carbon, first, second], pbc=(True, True, True))


def f5_host() -> MolecularCrystal:
    return _crystal(np.eye(3) * 12.0, ["C", "O"], [[2.0, 2.0, 2.0], [3.2, 2.0, 2.0]])


def f6_frames() -> tuple[MolecularCrystal, MolecularCrystal]:
    lattice = np.eye(3) * 10.0
    first = _crystal(lattice, ["Na", "Cl"], [[2.0, 2.0, 2.0], [4.5, 2.0, 2.0]])
    second = _crystal(lattice, ["Na", "Cl"], [[2.2, 2.1, 2.0], [4.7, 2.1, 2.0]])
    return first, second


WATER = {
    "symbols": ["O", "H", "H"],
    "positions": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]],
    "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
}
