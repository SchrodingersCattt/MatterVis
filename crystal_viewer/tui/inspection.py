"""Deterministic focus resolution and analytical reads over an existing CrystalIR."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def resolve_atom_references(crystal, references: Iterable[str | int | dict[str, Any]]) -> tuple[int, ...]:
    """Resolve explicit atom references to display indices without guessing.

    Strings first match an exact ``display_copy_id`` and then an exact,
    case-sensitive source label. Integer references are ``source_index`` values.
    A mapping accepts exactly one of ``label``, ``source_index`` or
    ``display_copy_id``. Label and source references intentionally return every
    currently manifested periodic copy in CrystalIR order.
    """
    resolved: list[int] = []
    for reference in references:
        if isinstance(reference, int) and not isinstance(reference, bool):
            matches = [index for index, atom in enumerate(crystal.atoms) if atom.source_index == reference]
        elif isinstance(reference, str):
            exact_copy = [index for index, atom in enumerate(crystal.atoms) if atom.display_copy_id == reference]
            matches = exact_copy or [index for index, atom in enumerate(crystal.atoms) if atom.label == reference]
        elif isinstance(reference, dict):
            keys = set(reference)
            if len(keys) != 1 or not keys <= {"label", "source_index", "display_copy_id"}:
                raise ValueError("atom reference must contain exactly one of label, source_index, display_copy_id")
            matches = list(resolve_atom_references(crystal, [next(iter(reference.values()))]))
        else:
            raise TypeError("atom references must be strings, source indices, or reference mappings")
        if not matches:
            raise ValueError(f"no displayed atom matches {reference!r}")
        resolved.extend(matches)
    return tuple(dict.fromkeys(resolved))


def resolve_molecule_reference(crystal, reference: str | int | dict[str, Any]) -> tuple[int, ...]:
    """Resolve one molecule namespace to the matching displayed atom indices."""
    if isinstance(reference, int) and not isinstance(reference, bool):
        matches = [
            index for index, atom in enumerate(crystal.atoms)
            if atom.source_molecule_index == reference
        ]
    elif isinstance(reference, str):
        matches = [
            index for index, atom in enumerate(crystal.atoms)
            if atom.display_fragment_id == reference
        ]
    elif isinstance(reference, dict):
        keys = set(reference)
        if len(keys) != 1 or not keys <= {"source_molecule_index", "display_molecule_index", "display_fragment_id"}:
            raise ValueError(
                "molecule reference must contain exactly one of source_molecule_index, "
                "display_molecule_index, display_fragment_id"
            )
        key, value = next(iter(reference.items()))
        if key == "source_molecule_index":
            matches = [index for index, atom in enumerate(crystal.atoms) if atom.source_molecule_index == value]
        elif key == "display_molecule_index":
            matches = [index for index, atom in enumerate(crystal.atoms) if atom.molecule_index == value]
        else:
            matches = [index for index, atom in enumerate(crystal.atoms) if atom.display_fragment_id == value]
    else:
        raise TypeError("molecule reference must be an index, fragment ID, or reference mapping")
    if not matches:
        raise ValueError(f"no displayed molecule matches {reference!r}")
    return tuple(matches)


def inspect_atoms(crystal, references: Iterable[str | int | dict[str, Any]] | None = None, *, show_minor: bool = False) -> dict[str, Any]:
    """Return truthful display/source provenance for current atom records."""
    indices = tuple(range(crystal.n_atoms)) if references is None else resolve_atom_references(crystal, references)
    records = [_atom_record(crystal, index, show_minor=show_minor) for index in indices]
    return {"atoms": records, "count": len(records)}


def inspect_molecules(crystal, reference: str | int | dict[str, Any] | None = None, *, show_minor: bool = False) -> dict[str, Any]:
    """Return source-MolCrysKit and displayed-fragment facts without re-grouping."""
    by_source: dict[int, list[int]] = defaultdict(list)
    for index, atom in enumerate(crystal.atoms):
        if atom.source_molecule_index >= 0:
            by_source[atom.source_molecule_index].append(index)

    if reference is not None:
        selected = set(resolve_molecule_reference(crystal, reference))
        by_source = {
            source_index: [index for index in indices if index in selected]
            for source_index, indices in by_source.items()
        }
        by_source = {source_index: indices for source_index, indices in by_source.items() if indices}

    molecules: list[dict[str, Any]] = []
    for source_index, indices in sorted(by_source.items()):
        visible = [index for index in indices if show_minor or not crystal.atoms[index].is_minor]
        display_instances: dict[int, list[int]] = defaultdict(list)
        for index in indices:
            display_instances[crystal.atoms[index].molecule_index].append(index)
        molecules.append({
            "source_molecule_index": source_index,
            "grouping_source": "molcrys_kit.mol_indices",
            "species_id": crystal.source_molecule_species.get(source_index),
            "per_formula_unit": (
                crystal.per_formula_unit.get(crystal.source_molecule_species[source_index])
                if source_index in crystal.source_molecule_species
                else None
            ),
            "source_member_indices": list(crystal.source_molecules.get(source_index, ())),
            "member_scope": "source" if source_index in crystal.source_molecules else "display_intersection",
            "complete": source_index in crystal.source_molecules,
            "display_instances": [
                {
                    "display_molecule_index": molecule_index,
                    "display_fragment_id": crystal.atoms[member_indices[0]].display_fragment_id,
                    "member_copy_ids": [crystal.atoms[index].display_copy_id for index in member_indices],
                }
                for molecule_index, member_indices in sorted(display_instances.items())
            ],
            "members": [_atom_record(crystal, index, show_minor=show_minor) for index in indices],
            "visible_member_count": len(visible),
        })
    return {"molecules": molecules, "count": len(molecules)}


def _atom_record(crystal, index: int, *, show_minor: bool) -> dict[str, Any]:
    atom = crystal.atoms[index]
    return {
        "display_index": index,
        "display_copy_id": atom.display_copy_id,
        "label": atom.label,
        "element": atom.element,
        "source_index": atom.source_index,
        "source_instance_id": atom.source_instance_id,
        "symmetry_operation_index": atom.symmetry_operation_index,
        "image_shift": list(atom.image_shift),
        "occupancy": atom.occupancy,
        "partial_occupancy": atom.occupancy < 0.999,
        "disorder_group": atom.disorder_group,
        "render_classification": "minor" if atom.is_minor else "not_minor",
        "classification_provenance_available": False,
        "source_molecule_index": atom.source_molecule_index,
        "display_molecule_index": atom.molecule_index,
        "display_fragment_id": atom.display_fragment_id,
        "visible": bool(show_minor or not atom.is_minor),
        "hidden_reason": None if show_minor or not atom.is_minor else "minor_filtered",
        "cartesian": [float(value) for value in atom.cart],
        "fractional": [float(value) for value in atom.frac],
    }


__all__ = [
    "inspect_atoms",
    "inspect_molecules",
    "resolve_atom_references",
    "resolve_molecule_reference",
]