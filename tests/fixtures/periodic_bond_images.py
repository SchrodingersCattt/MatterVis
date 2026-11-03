"""Synthetic periodic dimers for unit-cell display-image tests.

The structures are geometric fixtures: a 1.4 Å C–C contact, optionally
with one hydrogen, in a cubic or triclinic cell. They are not published
structures.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from ase.geometry import cellpar_to_cell

CUBIC_CELL = np.eye(3) * 10.0
BOND_LENGTH_A = 1.4
# Home-cell centres used by the committed cubic fixtures, in angstrom.
CROSSING_OFFSETS_A = (-0.9, 0.5, 9.1, 10.5)
FIXTURE_DIR = Path(__file__).resolve().parent


def cubic_lattice() -> np.ndarray:
    return np.array(CUBIC_CELL, dtype=float, copy=True)


def triclinic_lattice() -> np.ndarray:
    """10 × 12 × 11 Å cell with angles 80°, 75°, and 100°."""
    return np.asarray(cellpar_to_cell([10.0, 12.0, 11.0, 80.0, 75.0, 100.0]), dtype=float)


def crossing_fractional(axis: int, *, wrapped: bool) -> np.ndarray:
    """Two fractional sites, 0.14 apart, crossing ``axis``."""
    first = np.full(3, 0.5)
    second = np.full(3, 0.5)
    first[axis] = 0.05
    second[axis] = 0.91 if wrapped else -0.09
    return np.vstack((first, second))


def interior_fractional() -> np.ndarray:
    """A 1.4 Å C–C contact wholly inside the home cell."""
    return np.array([[0.40, 0.50, 0.50], [0.54, 0.50, 0.50]], dtype=float)


def hydrogen_fractional(*, wrapped: bool) -> np.ndarray:
    """C–C contact crossing x, with H 1.1 Å from the second carbon."""
    carbons = crossing_fractional(0, wrapped=wrapped)
    hydrogen = np.array(carbons[1], dtype=float, copy=True)
    hydrogen[0] += 0.11
    return np.vstack((carbons, hydrogen))


def translate_sites(
    fractional: np.ndarray,
    shift: tuple[int, int, int],
    *,
    which: tuple[int, ...] | None = None,
) -> np.ndarray:
    """Add one integer cell to selected rows. ``which`` defaults to every row."""
    out = np.array(fractional, dtype=float, copy=True)
    rows = range(len(out)) if which is None else which
    out[list(rows)] += np.asarray(shift, dtype=float)
    return out


def cartesian(fractional: np.ndarray, lattice: np.ndarray) -> np.ndarray:
    return np.asarray(fractional, dtype=float) @ np.asarray(lattice, dtype=float)


def write_extxyz(
    path: Path,
    fractional: np.ndarray,
    lattice: np.ndarray,
    species: list[str] | None = None,
) -> Path:
    """Write one periodic ExtXYZ frame. Coordinates are Cartesian angstrom."""
    coords = cartesian(fractional, lattice)
    labels = species or ["C"] * len(coords)
    if len(labels) != len(coords):
        raise ValueError("species and coordinates must have the same length")
    flat = " ".join(f"{value:.12g}" for value in np.asarray(lattice, dtype=float).reshape(-1))
    lines = [
        str(len(coords)),
        f'Lattice="{flat}" Properties=species:S:1:pos:R:3 pbc="T T T"',
    ]
    for label, coord in zip(labels, coords):
        lines.append(
            f"{label} {coord[0]:.12g} {coord[1]:.12g} {coord[2]:.12g}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def committed_fixture(name: str) -> Path:
    path = FIXTURE_DIR / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path
