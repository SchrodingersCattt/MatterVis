"""Display-mode atom selection helpers.

Moved from ``scene/core.py`` per the layered design: display-mode
filtering is part of the render pipeline (scene assembly), not
scene-state persistence.

See ``agents/scene_api.md`` for the ``display_mode`` values.
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


def _continuous_components(
    ops: Any,
    atoms: list[dict[str, Any]],
    M: Any,
    cell: Any,
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    """Cluster atoms into connected components via bond graph, then
    reassemble each component into a continuous fractional-coordinate
    representation using P1 symmetry expansion.
    """
    from crystal_viewer.structure.formula_unit import cluster_atoms, assemble_component_p1

    atoms_out = [dict(atom) for atom in atoms]
    bond_pairs = ops.find_bonds(atoms_out, cell=cell)
    clusters = cluster_atoms(atoms_out, bonds=bond_pairs)
    ordered = [sorted(idxs) for _, idxs in sorted(clusters.items(), key=lambda item: min(item[1]))]
    legacy_M = np.asarray(M, dtype=float).T
    for idxs in ordered:
        atoms_out = assemble_component_p1(atoms_out, idxs, bond_pairs, legacy_M)
    return atoms_out, ordered


def _best_component_shift_frac(
    component_atoms: list[dict[str, Any]],
) -> np.ndarray:
    """Find the optimal integer fractional shift that centres a component
    inside [0, 1] along every axis.
    """
    fracs = np.array([atom["frac"] for atom in component_atoms], dtype=float)
    shifts = (
        np.array(
            np.meshgrid(
                np.arange(-2, 3, dtype=float),
                np.arange(-2, 3, dtype=float),
                np.arange(-2, 3, dtype=float),
                indexing="ij",
            )
        )
        .reshape(3, -1)
        .T
    )
    shifted = fracs[None, :, :] + shifts[:, None, :]
    lower = np.clip(-shifted, 0.0, None)
    upper = np.clip(shifted - 1.0, 0.0, None)
    outside_penalty = np.sum(lower * lower + upper * upper, axis=(1, 2))
    center_penalty = np.linalg.norm(shifted.mean(axis=1) - 0.5, axis=1)
    scores = outside_penalty * 50.0 + center_penalty
    return shifts[int(np.argmin(scores))]


def _translate_component_frac(
    atoms: list[dict[str, Any]],
    idxs: list[int],
    shift_frac: Any,
    M: Any,
) -> list[dict[str, Any]]:
    """Translate a subset of atoms by ``shift_frac`` (fractional)."""
    shift_frac_arr = np.array(shift_frac, dtype=float)
    shift_cart = frac_to_cart(shift_frac_arr, np.asarray(M, dtype=float))
    translated = [dict(atom) for atom in atoms]
    for idx in idxs:
        translated[idx]["frac"] = np.array(translated[idx]["frac"], dtype=float) + shift_frac_arr
        translated[idx]["cart"] = np.array(translated[idx]["cart"], dtype=float) + shift_cart
    return translated


def _whole_components_in_box(
    ops: Any,
    atoms: list[dict[str, Any]],
    M: Any,
    cell: Any,
) -> list[dict[str, Any]]:
    """Cluster atoms, recentre each cluster inside [0,1], expand via P1."""
    atoms_out, components = _continuous_components(ops, atoms, M, cell)
    for idxs in components:
        component_atoms = [atoms_out[idx] for idx in idxs]
        shift_frac = _best_component_shift_frac(component_atoms)
        atoms_out = _translate_component_frac(atoms_out, idxs, shift_frac, M)
    return atoms_out


def selected_atoms_for_mode(
    ops: Any,
    atoms: list[dict[str, Any]],
    M: Any,
    cell: Any,
    display_mode: str = "formula_unit",
    formula_unit_atoms: list[dict[str, Any]] | None = None,
    unwrapped_atoms: list[dict[str, Any]] | None = None,
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
        base = [dict(atom) for atom in continuous_atoms]
        return expand_boundary_replicas(base, M)
    if display_mode == "asymmetric_unit":
        return _asymmetric_unit_atoms(continuous_atoms)
    if display_mode == "cluster":
        return [dict(atom) for atom in atoms]
    # formula_unit: defer to MolCrysKit
    if formula_unit_atoms is not None:
        return [dict(atom) for atom in formula_unit_atoms]
    from crystal_viewer.structure import molcrys_bridge
    return molcrys_bridge.select_formula_unit(atoms, M)
