"""Adapt explicit source molecule partitions to MolCrysKit identification.

MolCrysKit ``identify_molecules`` does not accept a source partition. It
does accept ``exclude_indices``. This module passes the atoms outside
each source id through that argument and keeps the molecules whose
indices already lie inside the partition. Those indices stay the ones
MolCrysKit wrote. They are not rewritten here.

An identifier with an explicit ``molecule_indices`` or
``source_partitions`` parameter is called once on the whole frame.
An identifier with neither that parameter nor ``exclude_indices`` is
still called on each slice. Only then are partition-local ``left``,
``right``, ``atom_indices``, and ``bond_pairs`` mapped back to the
frame. Any other bond-record field whose value is a local atom index
is rejected instead of being left as a slice index.
"""
from __future__ import annotations

import inspect

import numpy as np

_UPSTREAM_PARTITION_PARAMETERS = ("molecule_indices", "source_partitions")
_LOCAL_INDEX_KEYS = ("left", "right")
# Fields MolCrysKit stores on a bond record that are not atom indices.
_PASSTHROUGH_RECORD_KEYS = frozenset(
    {
        "right_image_shift",
        "vector",
        "role",
        "bond_type",
        "bond_type_known",
        "order",
        "order_known",
        "kind",
    }
)


def _named_parameter(identify, name: str) -> bool:
    """Whether ``identify`` declares ``name`` as a real argument."""
    try:
        parameters = inspect.signature(identify).parameters
    except (TypeError, ValueError):
        return False
    parameter = parameters.get(name)
    if parameter is None:
        return False
    return parameter.kind is not inspect.Parameter.VAR_KEYWORD


def _partition_parameter(identify) -> str | None:
    """Name of an explicit upstream partition argument, if one exists."""
    for name in _UPSTREAM_PARTITION_PARAMETERS:
        if _named_parameter(identify, name):
            return name
    return None


def _validated_indices(atoms, molecule_indices) -> np.ndarray:
    values = np.asarray(molecule_indices)
    if values.shape != (len(atoms),) or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("molecule_index must contain one integer per atom")
    if np.any(values < 0):
        raise ValueError("molecule_index values must be non-negative")
    return values


def _grouped(values: np.ndarray) -> dict[int, list[int]]:
    partitions: dict[int, list[int]] = {}
    for index, source_id in enumerate(values):
        partitions.setdefault(int(source_id), []).append(index)
    return partitions


def _is_local_index(value, width: int) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        return False
    return 0 <= int(value) < width


def _remap_sliced_molecule(molecule, source_indices: np.ndarray):
    """Map slice-local indices back to the original frame.

    ``left`` and ``right`` are the index fields this adapter knows.
    Another integer field inside the slice is a local index this
    adapter does not own, so the call fails instead of publishing it.
    """
    info = molecule.info
    width = len(source_indices)
    info["atom_indices"] = [
        int(source_indices[int(index)]) for index in info["atom_indices"]
    ]
    info["bond_pairs"] = [
        (int(source_indices[int(left)]), int(source_indices[int(right)]))
        for left, right in info.get("bond_pairs", ())
    ]
    for record in info.get("bond_records", ()):
        for key, value in record.items():
            if key in _LOCAL_INDEX_KEYS or key in _PASSTHROUGH_RECORD_KEYS:
                continue
            if _is_local_index(value, width):
                raise ValueError(
                    "bond record field "
                    f"{key!r} is a partition-local index and is not remapped; "
                    "MolCrysKit must return frame indices"
                )
        record["left"] = int(source_indices[int(record["left"])])
        record["right"] = int(source_indices[int(record["right"])])
    return molecule


def _identify_excluding_other_partitions(atoms, identify, identify_kwargs, values):
    """Let MolCrysKit perceive each partition on the original frame.

    Atoms outside the partition are ``exclude_indices``. Returned
    ``atom_indices`` and bond-record endpoints are already frame
    indices. Components that are not inside the partition are the
    excluded atoms MolCrysKit still emits as isolates, and they are
    dropped.
    """
    partitions = _grouped(values)
    every = set(range(len(atoms)))
    caller_excluded = {int(index) for index in identify_kwargs.get("exclude_indices") or ()}
    molecules = []
    for source_id in sorted(partitions):
        members = set(partitions[source_id])
        kwargs = dict(identify_kwargs)
        kwargs["exclude_indices"] = (every - members) | caller_excluded
        for molecule in identify(atoms, **kwargs):
            indices = [int(index) for index in molecule.info.get("atom_indices", ())]
            if not indices or not set(indices) <= members:
                continue
            molecules.append(molecule)
    return molecules


def _identify_by_slicing(atoms, identify, identify_kwargs, values):
    molecules = []
    grouped = _grouped(values)
    for source_id in sorted(grouped):
        source_indices = np.asarray(grouped[source_id], dtype=int)
        for molecule in identify(atoms[source_indices], **identify_kwargs):
            molecules.append(_remap_sliced_molecule(molecule, source_indices))
    return molecules


def identify_source_partitions(atoms, identify, identify_kwargs, molecule_indices):
    """Perceive bonds inside, never between, explicit source molecule IDs.

    MolCrysKit owns bond perception and periodic unwrapping. When it
    accepts ``exclude_indices``, this function uses that argument on the
    full frame so the returned indices stay MolCrysKit's. Slicing is
    only the fallback for an identifier that has no exclusion argument
    and no partition argument.
    """
    values = _validated_indices(atoms, molecule_indices)
    parameter = _partition_parameter(identify)
    if parameter is not None:
        return list(identify(atoms, **identify_kwargs, **{parameter: values}))
    if _named_parameter(identify, "exclude_indices"):
        return _identify_excluding_other_partitions(
            atoms, identify, identify_kwargs, values
        )
    return _identify_by_slicing(atoms, identify, identify_kwargs, values)
