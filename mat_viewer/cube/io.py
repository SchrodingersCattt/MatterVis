"""Pure Gaussian/CP2K Cube data records and parsing.

This module imports NumPy only.  Plotly and scikit-image belong to optional
rendering adapters and are never loaded by structure inspection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from ..config.colors import CUBE_ELEMENT_SYMBOLS

BOHR_TO_ANGSTROM = 0.529177210903


@dataclass
class CubeAtom:
    atomic_number: int
    charge: float
    coord: np.ndarray

    @property
    def element(self) -> str:
        return CUBE_ELEMENT_SYMBOLS.get(self.atomic_number, str(self.atomic_number))


@dataclass
class CubeData:
    title: str
    comment: str
    atoms: list[CubeAtom]
    origin: np.ndarray
    axes: np.ndarray
    values: np.ndarray
    path: Path

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(value) for value in self.values.shape)

    @property
    def lattice(self) -> np.ndarray:
        """3x3 lattice matrix with cell vectors as rows, in Angstrom."""
        return self.axes * np.asarray(self.shape, dtype=float)[:, None]


def tile_cube(
    cube: CubeData,
    neg: tuple[int, int, int],
    pos: tuple[int, int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Tile cube values across periodic images and return values and origin."""
    reps = tuple(neg[index] + pos[index] for index in range(3))
    if any(repetition <= 0 for repetition in reps):
        raise ValueError(f"reps must be positive, got {reps}")
    values = np.tile(cube.values, reps)
    shift = sum(neg[index] * cube.axes[index] * cube.shape[index] for index in range(3))
    return values, cube.origin - shift


def tile_cube_data(
    cube: CubeData,
    neg: tuple[int, int, int],
    pos: tuple[int, int, int],
) -> CubeData:
    """Return a new CubeData with values tiled over periodic images."""
    values, origin = tile_cube(cube, neg, pos)
    return CubeData(
        title=cube.title,
        comment=cube.comment,
        atoms=list(cube.atoms),
        origin=origin,
        axes=np.array(cube.axes, dtype=float),
        values=values,
        path=cube.path,
    )


def _as_angstrom(vector: Iterable[float]) -> np.ndarray:
    return np.asarray(list(vector), dtype=float) * BOHR_TO_ANGSTROM


def read_cube(path: str | Path) -> CubeData:
    """Read Gaussian/CP2K Cube coordinates and axes in Angstrom."""
    cube_path = Path(path)
    with cube_path.open("r", encoding="utf-8", errors="replace") as handle:
        title = handle.readline().rstrip()
        comment = handle.readline().rstrip()
        atom_line = handle.readline().split()
        atom_count = abs(int(atom_line[0]))
        origin = _as_angstrom(float(value) for value in atom_line[1:4])

        shape: list[int] = []
        axes: list[np.ndarray] = []
        for _ in range(3):
            parts = handle.readline().split()
            shape.append(abs(int(parts[0])))
            axes.append(_as_angstrom(float(value) for value in parts[1:4]))

        atoms: list[CubeAtom] = []
        for _ in range(atom_count):
            parts = handle.readline().split()
            atoms.append(
                CubeAtom(
                    atomic_number=int(parts[0]),
                    charge=float(parts[1]),
                    coord=_as_angstrom(float(value) for value in parts[2:5]),
                )
            )
        values = np.fromiter(
            (float(value) for line in handle for value in line.split()), dtype=float
        )

    expected = int(np.prod(shape))
    if values.size != expected:
        raise ValueError(
            f"{cube_path} contains {values.size} values, expected {expected}"
        )
    return CubeData(
        title=title,
        comment=comment,
        atoms=atoms,
        origin=origin,
        axes=np.asarray(axes, dtype=float),
        values=values.reshape(shape),
        path=cube_path,
    )


def cube_grid(
    cube: CubeData, stride: int = 1
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return flattened x/y/z/value arrays, optionally downsampled."""
    stride = max(1, int(stride))
    values = cube.values[::stride, ::stride, ::stride]
    ii, jj, kk = np.indices(values.shape, dtype=float)
    coordinates = (
        cube.origin[:, None, None, None]
        + ii[None, ...] * cube.axes[0, :, None, None, None] * stride
        + jj[None, ...] * cube.axes[1, :, None, None, None] * stride
        + kk[None, ...] * cube.axes[2, :, None, None, None] * stride
    )
    return (
        coordinates[0].ravel(),
        coordinates[1].ravel(),
        coordinates[2].ravel(),
        values.ravel(),
    )


def default_isovalue(values: np.ndarray, percentile: float = 98.5) -> float:
    """Choose a robust isovalue from nonzero absolute values."""
    array = np.asarray(values, dtype=float)
    nonzero = np.abs(array[np.nonzero(array)])
    if nonzero.size == 0:
        raise ValueError("Cube values are all zero")
    candidate = float(np.percentile(nonzero, percentile))
    phase_limits = []
    positive_max = float(np.max(array))
    negative_max = float(-np.min(array))
    if positive_max > 0.0:
        phase_limits.append(positive_max)
    if negative_max > 0.0:
        phase_limits.append(negative_max)
    # marching_cubes requires a level strictly inside the scalar range. Keep a
    # small numerical margin so float32 conversion cannot collapse it to an
    # endpoint; for signed orbitals this also keeps both phases drawable.
    interior_limit = min(phase_limits) * 0.95
    return min(candidate, interior_limit)


__all__ = [
    "BOHR_TO_ANGSTROM",
    "CubeAtom",
    "CubeData",
    "cube_grid",
    "default_isovalue",
    "read_cube",
    "tile_cube",
    "tile_cube_data",
]
