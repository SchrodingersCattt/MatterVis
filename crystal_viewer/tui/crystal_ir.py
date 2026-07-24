"""CrystalIR — intermediate representation for crystal structures.

This is the single source of truth between the loader and all consumers
(terminal renderer, structured serializer, future agent APIs). It is
renderer-agnostic: carries enough chemistry/geometry for both visual
and semantic outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class Lattice:
    """Unit cell parameters + orthogonalization matrix."""

    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float
    matrix: np.ndarray  # 3×3 orthogonalization (rows = a, b, c vectors)

    @property
    def volume(self) -> float:
        return abs(np.linalg.det(self.matrix))

    @property
    def vectors(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Lattice vectors a, b, c as 1D arrays."""
        return self.matrix[0], self.matrix[1], self.matrix[2]

    def cell_vertices(self) -> np.ndarray:
        """8 corners of the parallelepiped in Cartesian coords, shape (8, 3)."""
        a, b, c = self.vectors
        origin = np.zeros(3)
        return np.array([
            origin, a, b, c,
            a + b, a + c, b + c,
            a + b + c,
        ])

    def cell_edges(self) -> list[tuple[int, int]]:
        """12 edges of the parallelepiped as (vertex_idx, vertex_idx) pairs."""
        return [
            (0, 1), (0, 2), (0, 3),  # from origin
            (1, 4), (1, 5),           # from a
            (2, 4), (2, 6),           # from b
            (3, 5), (3, 6),           # from c
            (4, 7), (5, 7), (6, 7),   # to a+b+c
        ]


@dataclass
class AtomIR:
    """Single atom in the crystal."""

    element: str
    cart: np.ndarray       # Cartesian position (3,)
    frac: np.ndarray       # Fractional coordinates (3,)
    label: str = ""
    occupancy: float = 1.0
    index: int = 0         # Index in the atoms list


@dataclass
class BondIR:
    """Bond between two atoms."""

    i: int       # Index of first atom
    j: int       # Index of second atom
    distance: float = 0.0


@dataclass
class CrystalIR:
    """Intermediate representation of a crystal structure.

    This carries enough data for both rendering (ASCII/TUI) and
    semantic serialization (structured output for agents).
    """

    # Identity
    title: str = ""
    formula: str = ""
    spacegroup: str = ""
    source_path: str = ""

    # Geometry
    lattice: Lattice | None = None
    atoms: list[AtomIR] = field(default_factory=list)
    bonds: list[BondIR] = field(default_factory=list)

    # Metadata (extensible)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Derived properties ──────────────────────────────────────────────

    @property
    def n_atoms(self) -> int:
        return len(self.atoms)

    @property
    def cart_coords(self) -> np.ndarray:
        """All Cartesian coordinates as (N, 3) array."""
        if not self.atoms:
            return np.empty((0, 3))
        return np.array([a.cart for a in self.atoms])

    @property
    def elements(self) -> list[str]:
        """Element symbols for each atom."""
        return [a.element for a in self.atoms]

    @property
    def unique_elements(self) -> list[str]:
        """Sorted unique element symbols."""
        return sorted(set(self.elements))

    @property
    def center_of_mass(self) -> np.ndarray:
        """Geometric center (unweighted)."""
        coords = self.cart_coords
        if len(coords) == 0:
            return np.zeros(3)
        return coords.mean(axis=0)

    def element_counts(self) -> dict[str, int]:
        """Count of each element."""
        counts: dict[str, int] = {}
        for a in self.atoms:
            counts[a.element] = counts.get(a.element, 0) + 1
        return counts
