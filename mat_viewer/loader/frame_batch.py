"""Canonical contiguous arrays shared by all high-throughput renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np


@dataclass(frozen=True, slots=True)
class FrameBatch:
    """One atomistic frame without format-specific or per-atom Python objects."""

    positions: np.ndarray
    atomic_numbers: np.ndarray
    atom_ids: np.ndarray | None
    origin: np.ndarray
    cell: np.ndarray
    pbc: np.ndarray
    timestep: int
    source_index: int
    info: dict[str, Any] | None = None
    atom_arrays: dict[str, np.ndarray] = field(default_factory=dict)
    atom_colors: np.ndarray | None = None

    def __post_init__(self) -> None:
        positions = np.ascontiguousarray(self.positions, dtype=np.float32)
        numbers = np.ascontiguousarray(self.atomic_numbers, dtype=np.uint8)
        origin = np.asarray(self.origin, dtype=np.float64)
        cell = np.asarray(self.cell, dtype=np.float64)
        pbc = np.asarray(self.pbc, dtype=bool)
        if positions.ndim != 2 or positions.shape[1:] != (3,):
            raise ValueError("positions must have shape (N, 3)")
        if numbers.shape != (len(positions),):
            raise ValueError("atomic_numbers must have shape (N,)")
        if origin.shape != (3,) or cell.shape != (3, 3) or pbc.shape != (3,):
            raise ValueError("origin, cell, and pbc must have shapes (3,), (3,3), (3,)")
        atom_ids = self.atom_ids
        if atom_ids is not None:
            atom_ids = np.ascontiguousarray(atom_ids, dtype=np.int64)
            if atom_ids.shape != (len(positions),):
                raise ValueError("atom_ids must have shape (N,)")
        atom_arrays: dict[str, np.ndarray] = {}
        for name, values in dict(self.atom_arrays or {}).items():
            array = np.ascontiguousarray(values)
            if array.ndim < 1 or len(array) != len(positions):
                raise ValueError(f"atom array {name!r} must begin with shape (N,...)")
            atom_arrays[str(name)] = array
        atom_colors = self.atom_colors
        if atom_colors is not None:
            atom_colors = np.ascontiguousarray(atom_colors, dtype=np.uint8)
            if atom_colors.shape != (len(positions), 3):
                raise ValueError("atom_colors must have shape (N,3)")
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "atomic_numbers", numbers)
        object.__setattr__(self, "atom_ids", atom_ids)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "cell", cell)
        object.__setattr__(self, "pbc", pbc)
        object.__setattr__(self, "info", dict(self.info or {}))
        object.__setattr__(self, "atom_arrays", atom_arrays)
        object.__setattr__(self, "atom_colors", atom_colors)

    @property
    def natoms(self) -> int:
        return int(self.positions.shape[0])

    @property
    def index(self) -> int:
        """Source-frame index used by annotation resolvers."""

        return int(self.source_index)


def frame_batch_from_ase(
    atoms: Any,
    *,
    source_index: int = 0,
    atom_array_names: Iterable[str] = (),
) -> FrameBatch:
    """Convert an ASE frame from any supported adapter to canonical arrays."""

    info = dict(atoms.info)
    atom_ids = None
    for key in ("id", "ids"):
        if key in atoms.arrays:
            atom_ids = atoms.arrays[key]
            break
    timestep = info.get("timestep", info.get("step", source_index))
    try:
        timestep = int(timestep)
    except (TypeError, ValueError):
        timestep = int(source_index)
    return FrameBatch(
        positions=atoms.get_positions(),
        atomic_numbers=atoms.get_atomic_numbers(),
        atom_ids=atom_ids,
        origin=np.zeros(3, dtype=np.float64),
        cell=np.asarray(atoms.cell.array, dtype=np.float64),
        pbc=np.asarray(atoms.pbc, dtype=bool),
        timestep=timestep,
        source_index=int(source_index),
        info={"frame_index": int(source_index), **info},
        atom_arrays={
            name: np.asarray(atoms.arrays[name])
            for name in atom_array_names
            if name in atoms.arrays
        },
    )


def frame_box_corners(frame: FrameBatch) -> np.ndarray:
    """Return a periodic box, or the exact nonperiodic position bounds."""

    if np.any(frame.pbc) and abs(float(np.linalg.det(frame.cell))) > 1.0e-12:
        lower = frame.origin
        basis = frame.cell
    else:
        if not frame.natoms:
            return np.zeros((8, 3), dtype=np.float64)
        lower = np.min(frame.positions, axis=0)
        basis = np.diag(np.max(frame.positions, axis=0) - lower)
    fractions = np.asarray(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 1, 0],
            [1, 0, 1],
            [0, 1, 1],
            [1, 1, 1],
        ],
        dtype=np.float64,
    )
    return lower + fractions @ basis


__all__ = ["FrameBatch", "frame_batch_from_ase", "frame_box_corners"]
