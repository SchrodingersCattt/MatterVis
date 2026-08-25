"""Cell-boundary atom/fragment replica helpers.

Moved from ``scene/core.py`` per the layered design: boundary
replication is part of the render pipeline, not scene-state
persistence.

See ``docs/agents/scene_api.md`` for the ``display_mode="unit_cell"``
contract.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart

_PERIODIC_FACE_TOL: float = 3e-2
_SOURCE_IMAGE_TOL: float = 1e-5


def expand_boundary_replicas(
    atoms: list[dict[str, Any]],
    M: Any,
) -> list[dict[str, Any]]:
    """Add image-replica copies for cell-boundary atoms/fragments.

    The periodic-context set of each atom is determined in *canonical wrapped*
    fractional space.  Sites within 0.03 fractional units of a face receive
    the adjacent image (for example 0.99 -> -0.01 and 0.01 -> 1.01).

    For atoms that carry ``_source_molecule_index``, each member contributes
    its own complete face/edge/corner shift set and those sets are unioned;
    every resulting shift translates the *whole fragment*. This preserves
    complete chemistry without combining unrelated face signals from
    different members. Existing MCK continuous-image placement is handled by
    deriving translations from the current displayed fractional coordinate.

    Returns a new list; atoms inside ``(tol, 1-tol)`` along every axis
    are passed through unchanged.
    """
    if not atoms:
        return atoms
    M_arr = np.asarray(M, dtype=float)

    # ── helpers ──────────────────────────────────────────────────────

    def _target_images_for_frac(
        frac: Any,
    ) -> list[tuple[int, int, int]]:
        if frac is None:
            return [(0, 0, 0)]
        frac_arr = np.asarray(frac, dtype=float)
        if frac_arr.shape != (3,):
            return [(0, 0, 0)]
        per_axis: list[list[int]] = [[0], [0], [0]]
        for axis in range(3):
            f = float(frac_arr[axis])
            on_zero = -_SOURCE_IMAGE_TOL <= f <= _PERIODIC_FACE_TOL + _SOURCE_IMAGE_TOL
            on_one = (
                1.0 - _PERIODIC_FACE_TOL - _SOURCE_IMAGE_TOL
                <= f
                <= 1.0 + _SOURCE_IMAGE_TOL
            )
            if on_zero:
                per_axis[axis] = [0, 1]
            elif on_one:
                per_axis[axis] = [0, -1]
        out_shifts: list[tuple[int, int, int]] = []
        for sa in per_axis[0]:
            for sb in per_axis[1]:
                for sc in per_axis[2]:
                    out_shifts.append((sa, sb, sc))
        return out_shifts

    def _periodic_translations_for_atom(
        atom: dict[str, Any],
    ) -> set[tuple[int, int, int]]:
        displayed = np.asarray(atom.get("frac"), dtype=float)
        if displayed.shape != (3,) or not np.all(np.isfinite(displayed)):
            return set()
        # Decompose the current continuous/MCK coordinate into a wrapped
        # visual position plus its integer display image. This keeps an atom
        # already shown at 1.02 in the +1 image from spawning a +2 image: its
        # only additional near-face translation is -1, back to 0.02.
        current_image_arr = np.floor(displayed + _SOURCE_IMAGE_TOL).astype(int)
        displayed_wrapped = displayed - current_image_arr
        current_image = tuple(int(value) for value in current_image_arr)
        return {
            tuple(target[axis] - current_image[axis] for axis in range(3))
            for target in _target_images_for_frac(displayed_wrapped)
        }

    def _molecule_periodic_translations(
        molecule_atoms: list[dict[str, Any]],
    ) -> set[tuple[int, int, int]]:
        translations: set[tuple[int, int, int]] = set()
        for atom in molecule_atoms:
            translations.update(_periodic_translations_for_atom(atom))
        translations.discard((0, 0, 0))
        return translations

    def _spans_cell(molecule_atoms: list[dict[str, Any]]) -> bool:
        if any(
            int(atom.get("_periodic_component_rank", 0) or 0) > 0
            for atom in molecule_atoms
        ):
            return True
        fractions = np.asarray(
            [atom.get("frac") for atom in molecule_atoms], dtype=float
        )
        if fractions.ndim != 2 or fractions.shape[1:] != (3,):
            return False
        return bool(np.any(np.ptp(fractions, axis=0) >= 1.0 - _SOURCE_IMAGE_TOL))

    # ── main loop ────────────────────────────────────────────────────

    out: list[dict[str, Any]] = []
    grouped: dict[int, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for atom in atoms:
        mol_idx = atom.get("_source_molecule_index")
        if mol_idx is None:
            ungrouped.append(atom)
            continue
        try:
            grouped.setdefault(int(mol_idx), []).append(atom)
        except (TypeError, ValueError):
            ungrouped.append(atom)

    for molecule_atoms in grouped.values():
        if _spans_cell(molecule_atoms):
            for atom in molecule_atoms:
                wrapped = np.mod(
                    np.asarray(
                        atom.get("_wrapped_frac", atom.get("frac")), dtype=float
                    ),
                    1.0,
                )
                wrapped[np.isclose(wrapped, 1.0, rtol=0.0, atol=1e-9)] = 0.0
                copied = dict(atom)
                copied["frac"] = wrapped
                copied["cart"] = frac_to_cart(wrapped, M_arr)
                copied["_wrapped_frac"] = wrapped.copy()
                copied["_image_shift"] = (0, 0, 0)
                copied["_cell_spanning_component"] = True
                copied.pop("_is_boundary_replica", None)
                copied.pop("_is_fragment_boundary_replica", None)
                out.append(copied)
            # A periodic framework is not a finite molecule. Replicating its
            # members independently from a face tolerance creates orphan atoms
            # and asymmetric chunks outside the cell. Scene assembly adds the
            # exact neighbouring context from signed MCK BondRecords instead.
            continue
        out.extend(molecule_atoms)
        for effective in sorted(_molecule_periodic_translations(molecule_atoms)):
            shift_arr = np.array(effective, dtype=float)
            shift_cart = frac_to_cart(shift_arr, M_arr)
            for atom in molecule_atoms:
                frac = np.asarray(atom.get("frac"), dtype=float)
                replica = dict(atom)
                replica["frac"] = (
                    frac + shift_arr if frac.shape == (3,) else atom.get("frac")
                )
                replica["cart"] = np.asarray(atom.get("cart"), dtype=float) + shift_cart
                replica["_image_shift"] = effective
                replica["_origin_label"] = atom.get("_origin_label", atom.get("label"))
                replica["_is_boundary_replica"] = True
                replica["_is_fragment_boundary_replica"] = True
                out.append(replica)

    for atom in ungrouped:
        out.append(atom)
        for shift in sorted(_periodic_translations_for_atom(atom)):
            if shift == (0, 0, 0):
                continue
            shift_arr = np.array(shift, dtype=float)
            shift_cart = frac_to_cart(shift_arr, M_arr)
            frac = np.asarray(atom.get("frac"), dtype=float)
            replica = dict(atom)
            replica["frac"] = (
                frac + shift_arr if frac.shape == (3,) else atom.get("frac")
            )
            replica["cart"] = np.asarray(atom.get("cart"), dtype=float) + shift_cart
            replica["_image_shift"] = shift
            replica["_origin_label"] = atom.get("_origin_label", atom.get("label"))
            replica["_is_boundary_replica"] = True
            out.append(replica)
    return out
