"""Laboratory-frame updates for coordinate and bond edits.

MolCrysKit rotates Cartesian positions and leaves tensors such as ``u_cart``
in the input frame. Bond re-perception can also unwrap an atom across a cell
face without rebasing the copied image shift. These helpers apply that
geometry after the chemistry operations return. Site identities stay on the
source ids.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from molcrys_kit.constants.config import (
    KEY_FRAC_X,
    KEY_FRAC_Y,
    KEY_FRAC_Z,
    KEY_IMAGE_SHIFT,
    KEY_U_CART,
)
from molcrys_kit.operations import (
    partition_at_bond,
    rotate_fragment_in_crystal,
    rotate_molecule,
    translate_atom,
    translate_molecule,
)
from molcrys_kit.utils.geometry import get_rotation_matrix

from .errors import ValidationError

# Fractional residual, in lattice units, still treated as one integer image.
# Cartesian jumps on a nonperiodic axis use the same numeric tolerance in Å.
_LATTICE_IMAGE_RESIDUAL = 1e-6
_STALE_FRACTIONAL_KEYS = (KEY_FRAC_X, KEY_FRAC_Y, KEY_FRAC_Z)
_LABORATORY_VECTOR_ARRAYS = ("forces", "velocities", "momenta")
# Crystal-level copies of these arrays still describe the pre-unwrap frame.
STALE_REPERCEPTION_KEYS = frozenset({*_STALE_FRACTIONAL_KEYS, KEY_IMAGE_SHIFT})


def coordinate_edit(
    command: str,
    crystal,
    params: Mapping[str, Any],
    *,
    explicit_sites: Callable[[Mapping[str, Any]], set[str]],
):
    """Return the edited crystal, provenance details, and per-site rotations."""

    records = list(crystal.get_site_records())
    by_id = {str(record.site_id): record for record in records}
    if command == "rotate_fragment":
        return _rotate_fragment(crystal, params, records, by_id, explicit_sites)
    return _translate_or_rotate_molecule(command, crystal, params, records, by_id, explicit_sites)


def rebase_unwrapped_image_shifts(source, result) -> None:
    """Align image shifts with coordinates after bond-driven unwrapping.

    ``MolecularCrystal.from_ase`` copies per-site arrays, then
    ``identify_molecules`` can move an atom by an integer lattice vector when
    a new bond crosses a cell face. The copied ``image_shift`` and
    ``frac_x`` / ``frac_y`` / ``frac_z`` values still describe the previous
    unwrapping. Site identities stay on the source ids; only the image
    assignment is rebased, and the stale fractional arrays are dropped.
    """

    for molecule in result.molecules:
        _drop_stale_fractional_arrays(molecule)
    lattice = np.asarray(result.lattice, dtype=float)
    if lattice.shape != (3, 3):
        raise ValidationError("bond perception lattice must be a 3x3 matrix")
    try:
        inverse = np.linalg.inv(lattice)
    except np.linalg.LinAlgError as exc:
        raise ValidationError("bond perception lattice is singular") from exc
    periodic = np.asarray(result.pbc, dtype=bool)
    source_by_id = {str(record.site_id): record for record in source.get_site_records()}
    grouped: dict[int, list] = {}
    for record in result.get_site_records():
        grouped.setdefault(int(record.molecule_index), []).append(record)
    for molecule_index, molecule in enumerate(result.molecules):
        shifts = np.zeros((len(molecule), 3), dtype=int)
        seen: set[int] = set()
        for record in grouped.get(molecule_index, []):
            source_record = source_by_id.get(str(record.site_id))
            if source_record is None:
                raise ValidationError("bond perception lost a source site while rebasing images")
            delta = np.asarray(record.cartesian_position_A, dtype=float) - np.asarray(
                source_record.cartesian_position_A, dtype=float
            )
            fractional_delta = delta @ inverse
            image_delta = np.zeros(3, dtype=int)
            image_delta[periodic] = np.rint(fractional_delta[periodic]).astype(int)
            residual = fractional_delta - image_delta.astype(float)
            nonperiodic_jump = np.abs(delta[~periodic]) if np.any(~periodic) else np.zeros(0)
            moved_off_lattice = np.any(np.abs(residual[periodic]) > _LATTICE_IMAGE_RESIDUAL)
            if moved_off_lattice or np.any(nonperiodic_jump > _LATTICE_IMAGE_RESIDUAL):
                raise ValidationError(
                    "bond perception moved a site by a displacement that is not a lattice translation"
                )
            local_index = int(record.local_index)
            shifts[local_index] = np.asarray(source_record.image_shift, dtype=int) + image_delta
            seen.add(local_index)
        if seen != set(range(len(molecule))):
            raise ValidationError("bond perception image rebase did not cover every site")
        shifts[:, ~periodic] = 0
        molecule.set_array(KEY_IMAGE_SHIFT, shifts)


def _rotate_fragment(crystal, params, records, by_id, explicit_sites):
    sites = params.get("sites")
    if not isinstance(sites, (list, tuple)) or len(sites) != 2:
        raise ValidationError("rotate_fragment requires two ordered source sites")
    ordered = []
    for site in sites:
        parsed = explicit_sites({"sites": [site]})
        ordered.append(next(iter(parsed)))
    if any(site_id not in by_id for site_id in ordered):
        raise ValidationError("rotate_fragment references an unknown source site")
    left, right = (by_id[site_id] for site_id in ordered)
    if left.molecule_index != right.molecule_index:
        raise ValidationError("fragment bond sites must belong to the same molecule")
    angle = _finite_angle(params)
    moving_side = str(params.get("moving_side") or "j")
    if moving_side not in {"i", "j"}:
        raise ValidationError("moving_side must be 'i' or 'j'")
    molecule_index = int(left.molecule_index)
    local_i = int(left.local_index)
    local_j = int(right.local_index)
    result = rotate_fragment_in_crystal(
        crystal,
        molecule_index,
        local_i,
        local_j,
        angle,
        moving_side=moving_side,
    )
    axis = np.asarray(right.cartesian_position_A, dtype=float) - np.asarray(
        left.cartesian_position_A, dtype=float
    )
    rotation = get_rotation_matrix(axis, np.radians(angle))
    partition = partition_at_bond(crystal.molecules[molecule_index], local_i, local_j)
    moving = partition.moving_atoms if moving_side == "j" else partition.fixed_atoms
    _rotate_laboratory_arrays(result.molecules[molecule_index], moving, rotation)
    orientations = _orientations_for(records, molecule_index, moving, rotation)
    details = {"sites": ordered, "angle": angle, "moving_side": moving_side}
    return result, details, orientations


def _translate_or_rotate_molecule(command, crystal, params, records, by_id, explicit_sites):
    targets = explicit_sites(params)
    missing = sorted(targets - set(by_id))
    if missing:
        raise ValidationError("unknown site ids: " + ", ".join(missing))
    orientations: dict[int, tuple[tuple[float, float, float], ...]] = {}
    if command in {"translate_atom", "translate_molecule"}:
        vector = np.asarray(params.get("vector"), dtype=float)
        if vector.shape != (3,) or not np.all(np.isfinite(vector)):
            raise ValidationError("vector must contain three finite Cartesian displacements in Å")
        result = crystal
        if command == "translate_atom":
            for site_id in sorted(targets):
                record = by_id[site_id]
                result = translate_atom(
                    result,
                    int(record.molecule_index),
                    int(record.local_index),
                    vector,
                )
        else:
            for index in sorted({int(by_id[site_id].molecule_index) for site_id in targets}):
                result = translate_molecule(result, index, vector)
        details = {"site_ids": sorted(targets), "vector_A": vector.tolist()}
        return result, details, orientations
    axis = np.asarray(params.get("axis"), dtype=float)
    angle = _finite_angle(params)
    if axis.shape != (3,) or not np.all(np.isfinite(axis)) or float(np.linalg.norm(axis)) == 0.0:
        raise ValidationError("axis must be a non-zero finite 3-vector")
    center = str(params.get("center") or "com")
    if center not in {"com", "centroid"}:
        raise ValidationError("center must be 'com' or 'centroid'")
    rotation = get_rotation_matrix(axis, np.radians(angle))
    molecule_indices = sorted({int(by_id[site_id].molecule_index) for site_id in targets})
    result = crystal
    for index in molecule_indices:
        result = rotate_molecule(result, index, axis, angle, center=center)
    for index in molecule_indices:
        local_indices = tuple(range(len(result.molecules[index])))
        _rotate_laboratory_arrays(result.molecules[index], local_indices, rotation)
        orientations.update(_orientations_for(records, index, local_indices, rotation))
    details = {
        "site_ids": sorted(targets),
        "axis": axis.tolist(),
        "angle": angle,
        "center": center,
    }
    return result, details, orientations


def _finite_angle(params: Mapping[str, Any]) -> float:
    try:
        angle = float(params["angle"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("angle must be a finite number in degrees") from exc
    if not np.isfinite(angle):
        raise ValidationError("angle must be a finite number in degrees")
    return angle


def _rotation_tuple(rotation: np.ndarray) -> tuple[tuple[float, float, float], ...]:
    array = np.asarray(rotation, dtype=float)
    return tuple(tuple(float(value) for value in row) for row in array)


def _orientations_for(records, molecule_index: int, local_indices, rotation: np.ndarray):
    by_local = {
        (int(record.molecule_index), int(record.local_index)): int(record.global_index)
        for record in records
    }
    encoded = _rotation_tuple(rotation)
    return {by_local[(molecule_index, int(local))]: encoded for local in local_indices}


def _rotate_laboratory_arrays(molecule, local_indices, rotation: np.ndarray) -> None:
    """Rotate Cartesian tensors and vectors stored on the moved atoms."""

    selected = [int(index) for index in local_indices]
    if KEY_U_CART in molecule.arrays:
        values = np.array(molecule.arrays[KEY_U_CART], dtype=float, copy=True)
        if values.ndim == 2 and values.shape[1] == 9:
            for index in selected:
                row = values[index]
                if row.shape == (9,) and np.all(np.isfinite(row)):
                    tensor = row.reshape(3, 3)
                    values[index] = (rotation @ tensor @ rotation.T).reshape(9)
        elif values.ndim == 3 and values.shape[1:] == (3, 3):
            for index in selected:
                if np.all(np.isfinite(values[index])):
                    values[index] = rotation @ values[index] @ rotation.T
        else:
            raise ValidationError("u_cart must have shape (n, 9) or (n, 3, 3) to rotate")
        molecule.set_array(KEY_U_CART, values)
    for key in _LABORATORY_VECTOR_ARRAYS:
        if key not in molecule.arrays:
            continue
        values = np.array(molecule.arrays[key], dtype=float, copy=True)
        if values.shape != (len(molecule), 3):
            raise ValidationError(f"{key} must have shape (n, 3) to rotate with the molecule")
        for index in selected:
            if np.all(np.isfinite(values[index])):
                values[index] = rotation @ values[index]
        molecule.set_array(key, values)


def _drop_stale_fractional_arrays(atoms_obj) -> None:
    for key in _STALE_FRACTIONAL_KEYS:
        if key in atoms_obj.arrays:
            del atoms_obj.arrays[key]


__all__ = ["STALE_REPERCEPTION_KEYS", "coordinate_edit", "rebase_unwrapped_image_shifts"]
