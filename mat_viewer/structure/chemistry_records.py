"""Immutable MatterVis copies of MolCrysKit public chemistry reports.

These records contain no inference code. They let renderers and inspectors
consume stable chemical identities, bond semantics, entity dimensionality,
stereochemistry, warnings, and provenance without retaining mutable toolkit
objects or rebuilding chemistry from displayed coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AbsoluteStructureRecord:
    """One CIF absolute-structure parameter with its reported uncertainty."""

    method: str
    raw: str
    value: float
    standard_uncertainty: float | None


@dataclass(frozen=True)
class AtomChemistryRecord:
    atom_id: str
    source_index: int
    entity_id: str
    element: str
    isotope: int | None
    formal_charge: int | None
    radical_electrons: int
    implicit_hydrogens: int | None
    oxidation_state: int | None
    status: str
    stereo_descriptor: str | None = None
    stereo_kind: str | None = None
    stereo_status: str | None = None
    cip_order: tuple[str, ...] = ()
    stereo_reason: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class BondChemistryRecord:
    atom1_id: str
    atom2_id: str
    order: float | None
    kind: str
    aromatic: bool
    atom2_image_shift: tuple[int, int, int]
    stereochemistry: str | None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityChemistryRecord:
    entity_id: str
    kind: str
    dimension: int | None
    atom_ids: tuple[str, ...]
    net_charge: int | None
    status: str
    translation_generators: tuple[tuple[int, int, int], ...] = ()
    warnings: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrystalChemistryRecords:
    status: str
    atoms: tuple[AtomChemistryRecord, ...]
    bonds: tuple[BondChemistryRecord, ...]
    entities: tuple[EntityChemistryRecord, ...]
    warnings: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    source_names: tuple[tuple[str, str], ...] = ()
    absolute_configuration: str | None = None
    absolute_structure: tuple[AbsoluteStructureRecord, ...] = ()
    absolute_structure_details: str | None = None
    source: str = "molcrys_kit"

    def atom(self, atom_id: str) -> AtomChemistryRecord | None:
        return next((record for record in self.atoms if record.atom_id == atom_id), None)

    def entity(self, entity_id: str) -> EntityChemistryRecord | None:
        return next(
            (record for record in self.entities if record.entity_id == entity_id),
            None,
        )


__all__ = [
    "AbsoluteStructureRecord",
    "AtomChemistryRecord",
    "BondChemistryRecord",
    "CrystalChemistryRecords",
    "EntityChemistryRecord",
]
