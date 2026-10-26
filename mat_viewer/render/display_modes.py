"""Display-mode atom selection helpers.

Moved from ``scene/core.py`` per the layered design: display-mode
filtering is part of the render pipeline (scene assembly), not
scene-state persistence.

See ``docs/agents/scene_api.md`` for the ``display_mode`` values.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart

# Snap values that ``np.mod`` leaves on the 1-boundary back onto 0 so a
# crystallographic site is not stored twice, once at each end of the axis.
_WRAP_ONE_ATOL = 1e-9


def _wrap_fractional(frac: Any) -> np.ndarray | None:
    """Fold one fractional coordinate into the home cell, with 1 identified as 0."""
    arr = np.asarray(frac, dtype=float)
    if arr.shape != (3,) or not np.all(np.isfinite(arr)):
        return None
    wrapped = np.mod(arr, 1.0)
    near_zero = np.isclose(wrapped, 0.0, rtol=0.0, atol=_WRAP_ONE_ATOL)
    near_one = np.isclose(wrapped, 1.0, rtol=0.0, atol=_WRAP_ONE_ATOL)
    if np.any(near_zero) or np.any(near_one):
        wrapped = np.array(wrapped, dtype=float, copy=True)
        wrapped[near_zero | near_one] = 0.0
    return wrapped


def _independent_wrapped_sites(
    atoms: list[dict[str, Any]],
    M: Any,
) -> list[dict[str, Any]]:
    """Return one wrapped crystallographic site per input atom.

    Ionic and inorganic lattices are not finite molecules. Keeping the
    MolCrysKit molecule index would union every member's face shift and
    translate the whole framework, and a non-zero periodic rank would
    suppress those face images in favour of a one-sided coordination
    shell. Each site is therefore replicated on its own.
    """
    M_arr = np.asarray(M, dtype=float)
    out: list[dict[str, Any]] = []
    for fallback_index, atom in enumerate(atoms):
        frac = _wrap_fractional(atom.get("frac"))
        if frac is None:
            continue
        copied = dict(atom)
        copied["frac"] = frac
        copied["cart"] = frac_to_cart(frac, M_arr)
        copied["_wrapped_frac"] = np.array(frac, dtype=float, copy=True)
        copied["_image_shift"] = (0, 0, 0)
        copied.setdefault("_source_index", fallback_index)
        for key in (
            "_source_molecule_index",
            "_periodic_component_rank",
            "_unwrapped",
            "_cell_spanning_component",
            "_is_boundary_replica",
            "_is_fragment_boundary_replica",
            "_is_framework_context_replica",
            "_is_bonded_image_replica",
        ):
            copied.pop(key, None)
        out.append(copied)
    return out


def _asymmetric_unit_atoms(atoms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate atoms by label + element + disorder tags."""
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for atom in atoms:
        key = (
            atom.get("label"),
            atom.get("elem"),
            str(atom.get("dg", "")).strip(),
            str(atom.get("da", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(dict(atom))
    return selected


def _wrapped_unit_cell_atoms(
    atoms: list[dict[str, Any]],
    M: Any,
) -> list[dict[str, Any]]:
    """Return home-cell atom centres without periodic display replicas."""
    M_arr = np.asarray(M, dtype=float)
    out: list[dict[str, Any]] = []
    for source_index, atom in enumerate(atoms):
        source_frac = atom.get("_wrapped_frac", atom.get("frac"))
        wrapped = _wrap_fractional(source_frac)
        if wrapped is None:
            continue
        copied = dict(atom)
        copied["frac"] = wrapped
        copied["cart"] = frac_to_cart(wrapped, M_arr)
        copied["_wrapped_frac"] = wrapped.copy()
        copied["_image_shift"] = (0, 0, 0)
        copied["_strict_unit_cell"] = True
        copied.setdefault("_source_index", source_index)
        copied.pop("_is_boundary_replica", None)
        copied.pop("_is_fragment_boundary_replica", None)
        out.append(copied)
    return out


def selected_atoms_for_mode(
    ops: Any,
    atoms: list[dict[str, Any]],
    M: Any,
    cell: Any,
    display_mode: str = "formula_unit",
    formula_unit_atoms: list[dict[str, Any]] | None = None,
    unwrapped_atoms: list[dict[str, Any]] | None = None,
    include_boundary_replicas: bool = True,
    lattice_site_display: bool = False,
) -> list[dict[str, Any]]:
    """Return the drawable atom list for the given display mode.

    ``display_mode`` is one of:

    - ``"formula_unit"`` — single formula unit (default)
    - ``"unit_cell"`` — conventional cell with boundary replicas
    - ``"asymmetric_unit"`` — only the asymmetric unit
    - ``"cluster"`` — every parsed atom, no PBC processing

    ``lattice_site_display`` is set for extended ionic and inorganic
    solids. Those views use wrapped crystallographic sites. Unit-cell
    replicas are the symmetric 0/1 face, edge, and corner images of each
    site. Formula-unit centres are folded into the home cell instead of
    keeping a molecular unwrap that sits outside the box.
    """
    from .boundary_replicas import expand_boundary_replicas

    continuous_atoms = unwrapped_atoms if unwrapped_atoms else atoms
    if display_mode == "unit_cell":
        if lattice_site_display:
            sites = _independent_wrapped_sites(atoms, M)
            if not include_boundary_replicas:
                for site in sites:
                    site["_strict_unit_cell"] = True
                return sites
            return expand_boundary_replicas(sites, M)
        if not include_boundary_replicas:
            return _wrapped_unit_cell_atoms(atoms, M)
        base = [dict(atom) for atom in continuous_atoms]
        return expand_boundary_replicas(base, M)
    if display_mode == "asymmetric_unit":
        return _asymmetric_unit_atoms(continuous_atoms)
    if display_mode == "cluster":
        return [dict(atom) for atom in atoms]
    # formula_unit: defer to MolCrysKit
    if formula_unit_atoms is not None:
        chosen = [dict(atom) for atom in formula_unit_atoms]
    else:
        from mat_viewer.structure import molcrys_bridge
        chosen = molcrys_bridge.select_formula_unit(atoms, M)
    if lattice_site_display:
        return _independent_wrapped_sites(chosen, M)
    return chosen
