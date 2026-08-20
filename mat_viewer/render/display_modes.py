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
        frac = np.asarray(source_frac, dtype=float)
        if frac.shape != (3,) or not np.all(np.isfinite(frac)):
            continue
        wrapped = np.mod(frac, 1.0)
        wrapped[np.isclose(wrapped, 1.0, rtol=0.0, atol=1e-9)] = 0.0
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
) -> list[dict[str, Any]]:
    """Return the drawable atom list for the given display mode.

    ``display_mode`` is one of:

    - ``"formula_unit"`` — single formula unit (default)
    - ``"unit_cell"`` — conventional cell with boundary replicas
    - ``"asymmetric_unit"`` — only the asymmetric unit
    - ``"cluster"`` — every parsed atom, no PBC processing
    """
    from .boundary_replicas import expand_boundary_replicas

    continuous_atoms = unwrapped_atoms if unwrapped_atoms else atoms
    if display_mode == "unit_cell":
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
        return [dict(atom) for atom in formula_unit_atoms]
    from mat_viewer.structure import molcrys_bridge
    return molcrys_bridge.select_formula_unit(atoms, M)
