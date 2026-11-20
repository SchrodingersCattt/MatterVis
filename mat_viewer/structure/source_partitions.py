"""Adapt explicit source molecule partitions to MolCrysKit identification.

MolCrysKit ``identify_molecules`` does not accept a source partition. Until
it grows an explicit ``molecule_indices`` or ``source_partitions`` parameter,
this module calls it once per source id and remaps only partition-local
indices. Keys rewritten here are exactly bond-record ``left`` and ``right``
plus ``atom_indices`` and ``bond_pairs``. Other bond-record fields are left
as MolCrysKit wrote them. When the identifier advertises one of those
parameters, this module forwards the array and does not remap.
"""
from __future__ import annotations

import inspect

import numpy as np

_UPSTREAM_PARTITION_PARAMETERS = ("molecule_indices", "source_partitions")


def _partition_parameter(identify) -> str | None:
    """Name of an explicit upstream partition argument, if one exists."""
    try:
        parameters = inspect.signature(identify).parameters
    except (TypeError, ValueError):
        return None
    for name in _UPSTREAM_PARTITION_PARAMETERS:
        parameter = parameters.get(name)
        if parameter is None:
            continue
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        return name
    return None


def _validated_indices(atoms, molecule_indices) -> np.ndarray:
    values = np.asarray(molecule_indices)
    if values.shape != (len(atoms),) or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("molecule_index must contain one integer per atom")
    if np.any(values < 0):
        raise ValueError("molecule_index values must be non-negative")
    return values


def identify_source_partitions(atoms, identify, identify_kwargs, molecule_indices):
    """Perceive bonds inside, never between, explicit source molecule IDs.

    MolCrysKit owns bond perception and periodic unwrapping. When the
    identifier has no partition parameter, its result indices are relative
    to each slice, so remap only provenance indices back to the original ASE
    frame. A disconnected source partition may still yield multiple
    components. An identifier that accepts ``molecule_indices`` or
    ``source_partitions`` is called once on the whole frame and its indices
    are not rewritten here.
    """
    values = _validated_indices(atoms, molecule_indices)
    parameter = _partition_parameter(identify)
    if parameter is not None:
        return list(identify(atoms, **identify_kwargs, **{parameter: values}))
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
