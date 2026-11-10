"""In-memory bridge between MolCrysKit crystals and MatterVis bundles.

This path does not write a temporary CIF. Site records already produced
by MolCrysKit are projected into the loader, and ``analyze_crystal``
reads that same crystal instead of perceiving a second bond graph from
rendered spheres.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from ase import Atoms
from molcrys_kit.constants.config import (
    KEY_ASSEMBLY,
    KEY_ATOM_ID,
    KEY_DISORDER_GROUP,
    KEY_LABEL,
    KEY_OCCUPANCY,
)
from molcrys_kit.structures.crystal import MolecularCrystal
from molcrys_kit.structures.molecule import CrystalMolecule


def raw_atoms_from_crystal(crystal) -> list[dict[str, Any]]:
    """Return loader atom dicts from MolCrysKit's public site records."""

    from ..structure.cif_parse import _raw_atoms_from_crystal

    return [dict(atom) for atom in _raw_atoms_from_crystal(crystal)]


def loaded_crystal_from_crystal(
    crystal,
    *,
    name: str,
    title: str | None = None,
    source: str = "operation",
):
    """Build a render bundle from an existing crystal without a CIF round-trip."""

    from ..loader.bundle_builder import build_loaded_crystal_from_atoms
    from ..structure.cif_parse import cell_from_matrix
    from ..structure.molcrys_bridge import analyze_crystal

    matrix = np.asarray(crystal.lattice, dtype=float).copy()
    analysis = analyze_crystal(crystal)
    bundle = build_loaded_crystal_from_atoms(
        name=name,
        source_path="",
        raw_atoms=raw_atoms_from_crystal(crystal),
        cell=cell_from_matrix(matrix),
        M=matrix,
        title=title or name,
        source=source,
        molcrys_analysis=analysis,
    )
    pbc = [bool(value) for value in crystal.pbc]
    bundle.scene = dict(bundle.scene)
    bundle.scene["pbc"] = list(pbc)
    bundle.scene["scientific_source"] = "molecular-crystal"
    return bundle


def crystal_from_molecules(
    lattice,
    molecules: list,
    *,
    pbc: tuple[bool, bool, bool],
    metadata: dict | None = None,
) -> MolecularCrystal:
    """Construct a crystal from caller-owned molecule copies."""

    return MolecularCrystal(
        np.asarray(lattice, dtype=float).copy(),
        molecules,
        pbc=tuple(bool(value) for value in pbc),
        metadata=dict(metadata or {}),
    )


def molecule_from_spec(
    symbols: list[str],
    positions,
    *,
    lattice,
    pbc: tuple[bool, bool, bool],
    occupancies: list[float] | None = None,
    labels: list[str] | None = None,
    site_ids: list[str] | None = None,
) -> CrystalMolecule:
    """Build one molecule without inferring extra atoms."""

    coords = np.asarray(positions, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 3 or len(symbols) != len(coords):
        raise ValueError("molecule positions must be an (N, 3) array aligned with symbols")
    atoms = Atoms(
        symbols=[str(symbol) for symbol in symbols],
        positions=coords,
        cell=np.asarray(lattice, dtype=float),
        pbc=list(pbc),
    )
    count = len(atoms)
    atoms.set_array(
        KEY_OCCUPANCY,
        np.asarray(occupancies if occupancies is not None else [1.0] * count, dtype=float),
    )
    atoms.set_array(KEY_DISORDER_GROUP, np.zeros(count, dtype=int))
    atoms.set_array(KEY_ASSEMBLY, np.array([""] * count))
    label_values = labels if labels is not None else [str(symbol) for symbol in symbols]
    atoms.set_array(KEY_LABEL, np.array([str(value) for value in label_values]))
    if site_ids is not None:
        atoms.set_array(KEY_ATOM_ID, np.array([str(value) for value in site_ids], dtype=str))
    return CrystalMolecule(atoms, check_pbc=False)


def molecule_without_locals(molecule: CrystalMolecule, drop_local: set[int]) -> CrystalMolecule | None:
    """Return a copy with explicit local indices removed, or ``None`` if empty."""

    keep = [index for index in range(len(molecule)) if index not in drop_local]
    if not keep:
        return None
    sliced = molecule[keep]
    return CrystalMolecule(sliced, check_pbc=False)


def replace_symbols(molecule: CrystalMolecule, local_symbols: dict[int, str]) -> CrystalMolecule:
    """Return a copy whose selected local indices wear new element symbols."""

    copied = molecule.copy()
    symbols = list(copied.get_chemical_symbols())
    for local_index, symbol in local_symbols.items():
        symbols[int(local_index)] = str(symbol)
    copied.set_chemical_symbols(symbols)
    return copied


__all__ = [
    "crystal_from_molecules",
    "loaded_crystal_from_crystal",
    "molecule_from_spec",
    "molecule_without_locals",
    "raw_atoms_from_crystal",
    "replace_symbols",
]
