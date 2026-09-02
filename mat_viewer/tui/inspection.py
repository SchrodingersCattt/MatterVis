"""Deterministic focus resolution and analytical reads over an existing CrystalIR."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

import numpy as np

from ..math.pbc import nearest_image_vector_cart
from .text import terminal_text


def resolve_atom_references(crystal, references: Iterable[str | int | dict[str, Any]]) -> tuple[int, ...]:
    """Resolve explicit atom references to display indices without guessing.

    Strings first match an exact ``atom_id``, then ``display_copy_id``, and then
    an exact, case-sensitive source label. Integer references are
    ``source_index`` values. A mapping accepts exactly one declared namespace.
    Stable atom IDs, labels, and source references intentionally return every
    currently manifested periodic copy in CrystalIR order.
    """
    resolved: list[int] = []
    for reference in references:
        if isinstance(reference, int) and not isinstance(reference, bool):
            matches = [index for index, atom in enumerate(crystal.atoms) if atom.source_index == reference]
        elif isinstance(reference, str):
            exact_id = [index for index, atom in enumerate(crystal.atoms) if atom.atom_id == reference]
            exact_copy = [index for index, atom in enumerate(crystal.atoms) if atom.display_copy_id == reference]
            matches = exact_id or exact_copy or [index for index, atom in enumerate(crystal.atoms) if atom.label == reference]
        elif isinstance(reference, dict):
            keys = set(reference)
            if len(keys) != 1 or not keys <= {"atom_id", "label", "source_index", "display_copy_id"}:
                raise ValueError("atom reference must contain exactly one of atom_id, label, source_index, display_copy_id")
            key, value = next(iter(reference.items()))
            if key == "atom_id":
                if not isinstance(value, str):
                    raise TypeError("atom_id reference must be a string")
                matches = [index for index, atom in enumerate(crystal.atoms) if atom.atom_id == value]
            elif key == "label":
                if not isinstance(value, str):
                    raise TypeError("atom label reference must be a string")
                matches = [index for index, atom in enumerate(crystal.atoms) if atom.label == value]
            elif key == "display_copy_id":
                if not isinstance(value, str):
                    raise TypeError("display_copy_id reference must be a string")
                matches = [index for index, atom in enumerate(crystal.atoms) if atom.display_copy_id == value]
            else:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise TypeError("source_index reference must be an integer")
                matches = [index for index, atom in enumerate(crystal.atoms) if atom.source_index == value]
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


def inspect_local_geometry(
    crystal,
    reference: str | int | dict[str, Any],
    *,
    include_angles: bool = True,
) -> dict[str, Any]:
    """Read one atom's manifested bond neighborhood without judging it.

    The neighbor set is exactly the current ``CrystalIR.bonds`` topology. For
    periodic structures each bond reports both its manifested/direct length
    and the minimum-image length derived from the retained lattice. This is an
    observation primitive: it deliberately does not classify coordination,
    bond lengths, angles, or rings as chemically normal or abnormal.
    """
    indices = resolve_atom_references(crystal, [reference])
    if len(indices) != 1:
        raise ValueError("local geometry requires exactly one displayed atom; use display_copy_id to disambiguate")
    center_index = indices[0]
    center = crystal.atoms[center_index]
    neighbors: list[tuple[int, Any]] = []
    for bond in crystal.bonds:
        if bond.i == center_index:
            neighbors.append((bond.j, bond))
        elif bond.j == center_index:
            neighbors.append((bond.i, bond))
    neighbors.sort(key=lambda item: (crystal.atoms[item[0]].label, item[0]))

    bond_records: list[dict[str, Any]] = []
    vectors: dict[int, np.ndarray] = {}
    for neighbor_index, bond in neighbors:
        direct_vector = np.asarray(crystal.atoms[neighbor_index].cart - center.cart, dtype=float)
        mic_vector, image_shift = _minimum_image_vector(crystal, direct_vector)
        rendered_image_relation = (
            bond.image_relation
            if bond.i == center_index
            else tuple(-value for value in bond.image_relation)
        )
        vectors[neighbor_index] = mic_vector
        bond_records.append({
            "neighbor_display_index": neighbor_index,
            "neighbor_display_copy_id": terminal_text(crystal.atoms[neighbor_index].display_copy_id),
            "neighbor_label": terminal_text(crystal.atoms[neighbor_index].label),
            "neighbor_element": terminal_text(crystal.atoms[neighbor_index].element),
            "neighbor_atom_id": terminal_text(crystal.atoms[neighbor_index].atom_id),
            "rendered_distance": float(bond.distance),
            "direct_distance": float(np.linalg.norm(direct_vector)),
            "mic_distance": float(np.linalg.norm(mic_vector)),
            "nearest_image_shift": list(image_shift),
            "rendered_image_relation": list(rendered_image_relation),
        })

    angles: list[dict[str, Any]] = []
    if include_angles:
        for left_position, (left_index, _) in enumerate(neighbors):
            for right_index, _ in neighbors[left_position + 1:]:
                left = vectors[left_index]
                right = vectors[right_index]
                denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
                if denominator <= 1e-12:
                    continue
                value = float(np.degrees(np.arccos(np.clip(np.dot(left, right) / denominator, -1.0, 1.0))))
                angles.append({
                    "atoms": [
                        terminal_text(crystal.atoms[left_index].label),
                        terminal_text(center.label),
                        terminal_text(crystal.atoms[right_index].label),
                    ],
                    "angle_deg": value,
                })

    return {
        "center": {
            "display_index": center_index,
            "display_copy_id": terminal_text(center.display_copy_id),
            "label": terminal_text(center.label),
            "element": terminal_text(center.element),
            "atom_id": terminal_text(center.atom_id),
        },
        "coordination_number": len(neighbors),
        "bonds": bond_records,
        "angles": angles,
        "topology_provenance": {
            "source": str(crystal.metadata.get("bond_source", "manifested_crystal_ir")),
            "explicit_bond_table": bool(crystal.metadata.get("explicit_bond_table", False)),
            "neighbor_scope": "manifested_display_bonds",
            "angle_vectors": "minimum_image" if crystal.lattice is not None else "direct_cartesian",
        },
    }


def inspect_local_geometries(
    crystal,
    references: Iterable[str | int | dict[str, Any]] | None = None,
    *,
    include_angles: bool = True,
) -> dict[str, Any]:
    """Batch local-geometry reads while preserving one record per display atom.

    Omitting ``references`` inspects every manifested atom. Repeated source
    labels are expanded to their displayed copies and then de-duplicated in
    ``CrystalIR`` order. The result contains facts only, never anomaly labels.
    """
    indices = (
        tuple(range(crystal.n_atoms))
        if references is None
        else resolve_atom_references(crystal, references)
    )
    geometries = [
        inspect_local_geometry(
            crystal,
            {"display_copy_id": crystal.atoms[index].display_copy_id},
            include_angles=include_angles,
        )
        for index in indices
    ]
    return {"geometries": geometries, "count": len(geometries)}


def measure_distance(
    crystal,
    references: Iterable[str | int | dict[str, Any]],
    *,
    mode: str = "mic",
) -> dict[str, Any]:
    """Measure one displayed atom pair using direct or minimum-image geometry."""
    indices = _resolve_exact_count(crystal, references, 2, "distance")
    direct = np.asarray(crystal.atoms[indices[1]].cart - crystal.atoms[indices[0]].cart, dtype=float)
    mic, shift = _minimum_image_vector(crystal, direct)
    if mode not in {"direct", "mic"}:
        raise ValueError("distance mode must be 'direct' or 'mic'")
    vector = direct if mode == "direct" else mic
    return {
        "kind": "distance",
        "atoms": _labels(crystal, indices),
        "mode": mode,
        "value": float(np.linalg.norm(vector)),
        "unit": "angstrom",
        "vector": [float(value) for value in vector],
        "image_shifts": [[0, 0, 0], list(shift) if mode == "mic" else [0, 0, 0]],
    }


def measure_angle(
    crystal,
    references: Iterable[str | int | dict[str, Any]],
    *,
    mode: str = "mic",
) -> dict[str, Any]:
    """Measure A-B-C, anchoring minimum-image vectors at center B."""
    indices = _resolve_exact_count(crystal, references, 3, "angle")
    if mode not in {"direct", "mic"}:
        raise ValueError("angle mode must be 'direct' or 'mic'")
    left_direct = np.asarray(crystal.atoms[indices[0]].cart - crystal.atoms[indices[1]].cart, dtype=float)
    right_direct = np.asarray(crystal.atoms[indices[2]].cart - crystal.atoms[indices[1]].cart, dtype=float)
    left_mic, left_shift = _minimum_image_vector(crystal, left_direct)
    right_mic, right_shift = _minimum_image_vector(crystal, right_direct)
    left = left_direct if mode == "direct" else left_mic
    right = right_direct if mode == "direct" else right_mic
    value = _angle_degrees(left, right)
    return {
        "kind": "angle",
        "atoms": _labels(crystal, indices),
        "mode": mode,
        "value": value,
        "unit": "degree",
        "image_shifts": (
            [[0, 0, 0]] * 3
            if mode == "direct"
            else [list(left_shift), [0, 0, 0], list(right_shift)]
        ),
    }


def measure_dihedral(
    crystal,
    references: Iterable[str | int | dict[str, Any]],
    *,
    mode: str = "mic_chain",
) -> dict[str, Any]:
    """Measure signed A-B-C-D torsion using direct or chain-unwrapped geometry."""
    indices = _resolve_exact_count(crystal, references, 4, "dihedral")
    if mode not in {"direct", "mic_chain"}:
        raise ValueError("dihedral mode must be 'direct' or 'mic_chain'")
    coords = [np.asarray(crystal.atoms[index].cart, dtype=float) for index in indices]
    if mode == "direct":
        points = coords
        shifts = [(0, 0, 0)] * 4
    else:
        ba, shift_a = _minimum_image_vector(crystal, coords[0] - coords[1])
        bc, shift_c = _minimum_image_vector(crystal, coords[2] - coords[1])
        cd, shift_d_from_c = _minimum_image_vector(crystal, coords[3] - coords[2])
        points = [ba, np.zeros(3), bc, bc + cd]
        shifts = [shift_a, (0, 0, 0), shift_c, tuple(shift_c[i] + shift_d_from_c[i] for i in range(3))]
    return {
        "kind": "dihedral",
        "atoms": _labels(crystal, indices),
        "mode": mode,
        "value": _dihedral_degrees(*points),
        "unit": "degree",
        "image_shifts": [list(shift) for shift in shifts],
    }


def _resolve_exact_count(crystal, references, count: int, name: str) -> tuple[int, ...]:
    indices = resolve_atom_references(crystal, references)
    if len(indices) != count:
        raise ValueError(
            f"{name} requires exactly {count} displayed atoms; use display_copy_id to disambiguate"
        )
    if len(set(indices)) != count:
        raise ValueError(f"{name} references must be distinct")
    return indices


def _labels(crystal, indices: Iterable[int]) -> list[str]:
    return [terminal_text(crystal.atoms[index].label) for index in indices]


def _angle_degrees(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-12:
        raise ValueError("angle is undefined for coincident atoms")
    return float(np.degrees(np.arccos(np.clip(np.dot(left, right) / denominator, -1.0, 1.0))))


def _dihedral_degrees(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    first = a - b
    middle = c - b
    last = d - c
    middle_norm = float(np.linalg.norm(middle))
    if middle_norm <= 1e-12:
        raise ValueError("dihedral is undefined for coincident middle atoms")
    middle_unit = middle / middle_norm
    first_plane = first - np.dot(first, middle_unit) * middle_unit
    last_plane = last - np.dot(last, middle_unit) * middle_unit
    if np.linalg.norm(first_plane) <= 1e-12 or np.linalg.norm(last_plane) <= 1e-12:
        raise ValueError("dihedral is undefined for collinear atoms")
    x = float(np.dot(first_plane, last_plane))
    y = float(np.dot(np.cross(middle_unit, first_plane), last_plane))
    return float(np.degrees(np.arctan2(y, x)))


def _minimum_image_vector(crystal, direct_vector: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int]]:
    if crystal.lattice is None:
        return direct_vector.copy(), (0, 0, 0)
    matrix = np.asarray(crystal.lattice.matrix, dtype=float)
    try:
        vector, shift_array = nearest_image_vector_cart(direct_vector, matrix)
    except (np.linalg.LinAlgError, ValueError):
        return direct_vector.copy(), (0, 0, 0)
    return vector, tuple(int(value) for value in shift_array)


def _atom_record(crystal, index: int, *, show_minor: bool) -> dict[str, Any]:
    atom = crystal.atoms[index]
    return {
        "display_index": index,
        "display_copy_id": terminal_text(atom.display_copy_id),
        "label": terminal_text(atom.label),
        "element": terminal_text(atom.element),
        "source_index": atom.source_index,
        "source_instance_id": terminal_text(atom.source_instance_id),
        "symmetry_operation_index": atom.symmetry_operation_index,
        "image_shift": list(atom.image_shift),
        "occupancy": atom.occupancy,
        "partial_occupancy": atom.occupancy < 0.999,
        "disorder_group": atom.disorder_group,
        "source_disorder": terminal_text(atom.disorder),
        "render_classification": "minor" if atom.is_minor else "not_minor",
        "classification_provenance_available": False,
        "source_molecule_index": atom.source_molecule_index,
        "display_molecule_index": atom.molecule_index,
        "display_fragment_id": terminal_text(atom.display_fragment_id),
        "visible": bool(show_minor or not atom.is_minor),
        "hidden_reason": None if show_minor or not atom.is_minor else "minor_filtered",
        "cartesian": [float(value) for value in atom.cart],
        "fractional": [float(value) for value in atom.frac],
    }


__all__ = [
    "inspect_atoms",
    "inspect_local_geometries",
    "inspect_local_geometry",
    "inspect_molecules",
    "measure_angle",
    "measure_dihedral",
    "measure_distance",
    "resolve_atom_references",
    "resolve_molecule_reference",
]
