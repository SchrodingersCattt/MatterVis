"""Cell-boundary atom/fragment replica helpers.

Moved from ``scene/core.py`` per the layered design: boundary
replication is part of the render pipeline, not scene-state
persistence.

See ``agents/scene_api.md`` for the ``display_mode="unit_cell"``
contract.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart

_BOUNDARY_TOL: float = 1e-3  # fractional-coordinate tolerance for exact special positions
_FRAGMENT_FACE_TOL: float = 3e-2  # visual tolerance for whole fragments near a cell face


def expand_boundary_replicas(
    atoms: list[dict[str, Any]],
    M: Any,
) -> list[dict[str, Any]]:
    """Add image-replica copies for cell-boundary atoms/fragments.

    The boundary "mirror set" of each atom is determined in *canonical
    wrapped* fractional space (the original ``parse_asu`` coordinates,
    pinned to ``_wrapped_frac`` before MCK overwrites ``frac`` with its
    continuous unwrapped value).

    For atoms that carry ``_source_molecule_index``, the *fragment* is
    replicated rather than each atom independently.  Cart-space
    placement accounts for the integer *MCK drift* between the
    canonical wrapped centroid and the MCK home centroid.

    Returns a new list; atoms inside ``(tol, 1-tol)`` along every axis
    are passed through unchanged.
    """
    if not atoms:
        return atoms
    M_arr = np.asarray(M, dtype=float)

    # ── helpers ──────────────────────────────────────────────────────

    def _canonical_shifts_for_frac(
        frac: Any,
        tol: float,
    ) -> list[tuple[int, int, int]]:
        if frac is None:
            return [(0, 0, 0)]
        frac_arr = np.asarray(frac, dtype=float)
        if frac_arr.shape != (3,):
            return [(0, 0, 0)]
        per_axis: list[list[int]] = [[0], [0], [0]]
        for axis in range(3):
            f = float(frac_arr[axis])
            on_zero = -tol <= f <= tol
            on_one = 1.0 - tol <= f <= 1.0 + tol
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

    def _canonical_shifts_for_atom(
        atom: dict[str, Any],
    ) -> list[tuple[int, int, int]]:
        return _canonical_shifts_for_frac(
            atom.get("_wrapped_frac", atom.get("frac")),
            _BOUNDARY_TOL,
        )

    def _molecule_canonical_shifts(
        molecule_atoms: list[dict[str, Any]],
    ) -> set[tuple[int, int, int]]:
        shifts: set[tuple[int, int, int]] = set()
        wrapped_fracs: list[np.ndarray] = []
        for atom in molecule_atoms:
            for shift in _canonical_shifts_for_atom(atom):
                shifts.add(shift)
            frac = atom.get("_wrapped_frac", atom.get("frac"))
            if frac is None:
                continue
            frac_arr = np.asarray(frac, dtype=float)
            if frac_arr.shape == (3,):
                wrapped_fracs.append(frac_arr)
        if wrapped_fracs:
            centroid = np.mean(wrapped_fracs, axis=0)
            for shift in _canonical_shifts_for_frac(centroid, _FRAGMENT_FACE_TOL):
                shifts.add(shift)
        return shifts

    def _molecule_has_disorder(
        molecule_atoms: list[dict[str, Any]],
    ) -> bool:
        for atom in molecule_atoms:
            if "_is_minor" in atom or atom.get("_is_major"):
                return True
            dg = str(atom.get("dg") or "").strip()
            if dg not in ("", ".", "?", "0"):
                return True
            da = str(atom.get("da") or "").strip()
            if da not in ("", ".", "?"):
                return True
        return False

    def _molecule_display_face_shifts(
        molecule_atoms: list[dict[str, Any]],
    ) -> set[tuple[int, int, int]]:
        if not _molecule_has_disorder(molecule_atoms):
            return set()
        fracs: list[np.ndarray] = []
        for atom in molecule_atoms:
            frac = atom.get("frac")
            if frac is None:
                continue
            frac_arr = np.asarray(frac, dtype=float)
            if frac_arr.shape == (3,):
                fracs.append(frac_arr)
        if not fracs:
            return set()
        centroid = np.mean(fracs, axis=0)
        return set(_canonical_shifts_for_frac(centroid, _FRAGMENT_FACE_TOL))

    def _molecule_drift(
        molecule_atoms: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        wrapped: list[np.ndarray] = []
        mck: list[np.ndarray] = []
        for atom in molecule_atoms:
            w = atom.get("_wrapped_frac", atom.get("frac"))
            f = atom.get("frac")
            if w is None or f is None:
                continue
            w_arr = np.asarray(w, dtype=float)
            f_arr = np.asarray(f, dtype=float)
            if w_arr.shape != (3,) or f_arr.shape != (3,):
                continue
            wrapped.append(w_arr)
            mck.append(f_arr)
        if not wrapped:
            return (0, 0, 0)
        delta = np.mean(mck, axis=0) - np.mean(wrapped, axis=0)
        return tuple(int(np.floor(d + 0.5)) for d in delta)

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
        out.extend(molecule_atoms)
        canonical_shifts = _molecule_canonical_shifts(molecule_atoms)
        drift = _molecule_drift(molecule_atoms)
        effective_shifts: set[tuple[int, int, int]] = set()
        if drift in canonical_shifts:
            for cs in sorted(canonical_shifts):
                if cs == drift:
                    continue
                effective_shifts.add(
                    (cs[0] - drift[0], cs[1] - drift[1], cs[2] - drift[2])
                )
        effective_shifts.update(
            shift
            for shift in _molecule_display_face_shifts(molecule_atoms)
            if shift != (0, 0, 0)
        )
        for effective in sorted(effective_shifts):
            shift_arr = np.array(effective, dtype=float)
            shift_cart = frac_to_cart(shift_arr, M_arr)
            for atom in molecule_atoms:
                frac = np.asarray(atom.get("frac"), dtype=float)
                replica = dict(atom)
                replica["frac"] = (
                    frac + shift_arr if frac.shape == (3,) else atom.get("frac")
                )
                replica["cart"] = (
                    np.asarray(atom.get("cart"), dtype=float) + shift_cart
                )
                replica["_image_shift"] = effective
                replica["_origin_label"] = atom.get(
                    "_origin_label", atom.get("label")
                )
                replica["_is_boundary_replica"] = True
                replica["_is_fragment_boundary_replica"] = True
                out.append(replica)

    for atom in ungrouped:
        out.append(atom)
        for shift in _canonical_shifts_for_atom(atom):
            if shift == (0, 0, 0):
                continue
            shift_arr = np.array(shift, dtype=float)
            shift_cart = frac_to_cart(shift_arr, M_arr)
            frac = np.asarray(atom.get("frac"), dtype=float)
            replica = dict(atom)
            replica["frac"] = (
                frac + shift_arr if frac.shape == (3,) else atom.get("frac")
            )
            replica["cart"] = (
                np.asarray(atom.get("cart"), dtype=float) + shift_cart
            )
            replica["_image_shift"] = shift
            replica["_origin_label"] = atom.get("_origin_label", atom.get("label"))
            replica["_is_boundary_replica"] = True
            out.append(replica)
    return out
