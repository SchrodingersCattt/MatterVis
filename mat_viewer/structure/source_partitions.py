"""Adapt explicit source molecule partitions to MolCrysKit identification."""
from __future__ import annotations

import numpy as np


def identify_source_partitions(atoms, identify, identify_kwargs, molecule_indices):
    """Perceive bonds inside, never between, explicit source molecule IDs.

    MolCrysKit owns bond perception and periodic unwrapping. Its per-partition
    result indices are relative to that partition, so remap only provenance
    indices back to the original ASE frame before constructing the crystal.
    A disconnected source partition may still yield multiple MCK components.
    """
    values = np.asarray(molecule_indices)
    if values.shape != (len(atoms),) or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("molecule_index must contain one integer per atom")
    if np.any(values < 0):
        raise ValueError("molecule_index values must be non-negative")
    partitions: dict[int, list[int]] = {}
    for index, source_id in enumerate(values):
        partitions.setdefault(int(source_id), []).append(index)
    molecules = []
    for source_id in sorted(partitions):
        source_indices = np.asarray(partitions[source_id], dtype=int)
        for molecule in identify(atoms[source_indices], **identify_kwargs):
            info = molecule.info
            info["atom_indices"] = [
                int(source_indices[int(index)]) for index in info["atom_indices"]
            ]
            info["bond_pairs"] = [
                (int(source_indices[int(left)]), int(source_indices[int(right)]))
                for left, right in info.get("bond_pairs", ())
            ]
            for record in info.get("bond_records", ()):
                record["left"] = int(source_indices[int(record["left"])])
                record["right"] = int(source_indices[int(record["right"])])
            molecules.append(molecule)
    return molecules
