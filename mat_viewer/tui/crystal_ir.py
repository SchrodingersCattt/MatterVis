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
        return np.array(
            [
                origin,
                a,
                b,
                c,
                a + b,
                a + c,
                b + c,
                a + b + c,
            ]
        )

    def cell_edges(self) -> list[tuple[int, int]]:
        """12 edges of the parallelepiped as (vertex_idx, vertex_idx) pairs."""
        return [
            (0, 1),
            (0, 2),
            (0, 3),  # from origin
            (1, 4),
            (1, 5),  # from a
            (2, 4),
            (2, 6),  # from b
            (3, 5),
            (3, 6),  # from c
            (4, 7),
            (5, 7),
            (6, 7),  # to a+b+c
        ]


@dataclass
class AtomIR:
    """Single atom in the crystal."""

    element: str
    cart: np.ndarray  # Cartesian position (3,)
    frac: np.ndarray  # Fractional coordinates (3,)
    atom_id: str = ""  # Stable MolCrysKit atom identity
    label: str = ""  # CIF _atom_site_label (e.g. "Fe1", "O2", "C3A")
    occupancy: float = 1.0
    index: int = 0  # Index in the atoms list
    source_index: int = -1
    source_instance_id: str = ""
    symmetry_operation_index: int = 0
    image_shift: tuple[int, int, int] = (0, 0, 0)
    display_copy_id: str = ""
    source_molecule_index: int = -1
    display_fragment_id: str = ""

    # MCK-derived fields
    molecule_index: int = -1  # Which molecule this atom belongs to (-1 = unassigned)
    disorder_group: int = 0  # CIF disorder group (0 = ordered)
    is_minor: bool = False  # Minor disorder image (should be dimmed/hidden)

    @property
    def display_label(self) -> str:
        """Short label for terminal display (e.g. 'Fe1', 'O2')."""
        return self.label or self.element


@dataclass
class BondIR:
    """Bond between two atoms."""

    i: int  # Index of first atom
    j: int  # Index of second atom
    distance: float = 0.0
    start: np.ndarray | None = None
    end: np.ndarray | None = None
    start_display_copy_id: str = ""
    end_display_copy_id: str = ""
    image_relation: tuple[int, int, int] = (0, 0, 0)


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
    canonical_formula: str = ""
    canonical_composition: dict[str, int] = field(default_factory=dict)
    source_site_atom_count: int | None = None
    expanded_atom_count: int | None = None

    # Geometry
    lattice: Lattice | None = None
    atoms: list[AtomIR] = field(default_factory=list)
    bonds: list[BondIR] = field(default_factory=list)

    # MCK molecule grouping
    n_molecules: int = 0
    species_map: dict[str, list[int]] = field(default_factory=dict)
    source_molecules: dict[int, tuple[int, ...]] = field(default_factory=dict)
    source_molecule_species: dict[int, str] = field(default_factory=dict)

    # Metadata (extensible)
    metadata: dict[str, Any] = field(default_factory=dict)
    per_formula_unit: dict[str, int] = field(default_factory=dict)
    chemistry: Any | None = None

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


def filter_crystal(
    crystal: CrystalIR,
    keep_indices: set[int],
    *,
    collapse_source_images: bool = False,
) -> CrystalIR:
    """Return a subset and optionally collapse image bonds to source atoms."""
    old_to_new: dict[int, int] = {}
    new_atoms: list[AtomIR] = []
    for new_index, old_index in enumerate(sorted(keep_indices)):
        old_to_new[old_index] = new_index
        atom = crystal.atoms[old_index]
        new_atoms.append(
            AtomIR(
                element=atom.element,
                cart=atom.cart,
                frac=atom.frac,
                atom_id=atom.atom_id,
                label=atom.label,
                occupancy=atom.occupancy,
                index=new_index,
                source_index=atom.source_index,
                source_instance_id=atom.source_instance_id,
                symmetry_operation_index=atom.symmetry_operation_index,
                image_shift=atom.image_shift,
                display_copy_id=atom.display_copy_id,
                source_molecule_index=atom.source_molecule_index,
                display_fragment_id=atom.display_fragment_id,
                molecule_index=atom.molecule_index,
                disorder_group=atom.disorder_group,
                is_minor=atom.is_minor,
            )
        )

    source_to_new = {
        atom.source_index: index
        for index, atom in enumerate(new_atoms)
        if atom.source_index >= 0
    }
    new_bonds: list[BondIR] = []
    seen_pairs: set[tuple[int, int]] = set()
    for bond in crystal.bonds:
        if bond.i in old_to_new and bond.j in old_to_new:
            new_i = old_to_new[bond.i]
            new_j = old_to_new[bond.j]
        elif collapse_source_images:
            source_i = crystal.atoms[bond.i].source_index
            source_j = crystal.atoms[bond.j].source_index
            if source_i not in source_to_new or source_j not in source_to_new:
                continue
            new_i = source_to_new[source_i]
            new_j = source_to_new[source_j]
        else:
            continue
        if new_i == new_j:
            continue
        pair = tuple(sorted((new_i, new_j)))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        new_bonds.append(
            BondIR(
                i=new_i,
                j=new_j,
                distance=(
                    float(np.linalg.norm(new_atoms[new_j].cart - new_atoms[new_i].cart))
                    if collapse_source_images
                    else bond.distance
                ),
                start=bond.start,
                end=bond.end,
                start_display_copy_id=new_atoms[new_i].display_copy_id,
                end_display_copy_id=new_atoms[new_j].display_copy_id,
                image_relation=bond.image_relation,
            )
        )

    surviving_molecules = {
        atom.molecule_index for atom in new_atoms if atom.molecule_index >= 0
    }
    species_map = {
        species: [index for index in indices if index in surviving_molecules]
        for species, indices in crystal.species_map.items()
    }
    metadata = dict(crystal.metadata)
    metadata["display_atom_count"] = len(new_atoms)
    return CrystalIR(
        title=crystal.title,
        formula=_formula_from_atoms(new_atoms),
        spacegroup=crystal.spacegroup,
        source_path=crystal.source_path,
        canonical_formula=crystal.canonical_formula,
        canonical_composition=dict(crystal.canonical_composition),
        source_site_atom_count=crystal.source_site_atom_count,
        expanded_atom_count=crystal.expanded_atom_count,
        lattice=crystal.lattice,
        atoms=new_atoms,
        bonds=new_bonds,
        n_molecules=len(surviving_molecules),
        species_map={
            species: indices for species, indices in species_map.items() if indices
        },
        source_molecules=dict(crystal.source_molecules),
        source_molecule_species=dict(crystal.source_molecule_species),
        per_formula_unit=dict(crystal.per_formula_unit),
        metadata=metadata,
        chemistry=crystal.chemistry,
    )


def _formula_from_atoms(atoms: list[AtomIR]) -> str:
    counts: dict[str, int] = {}
    for atom in atoms:
        counts[atom.element] = counts.get(atom.element, 0) + 1

    def order(element: str) -> tuple[int, str]:
        if element == "C":
            return (0, element)
        if element == "H":
            return (1, element)
        return (2, element)

    return "".join(
        element if count == 1 else f"{element}{count}"
        for element, count in sorted(counts.items(), key=lambda item: order(item[0]))
    )
