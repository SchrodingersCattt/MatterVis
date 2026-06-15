from __future__ import annotations

import copy
import os
from types import SimpleNamespace
from typing import Any, Dict, Optional

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart

from ..structure.bonds import bonds_conflict, find_bonds
from ..structure.cif_parse import parse_asu
from ..style.disorder import atom_is_minor, bond_is_minor, disorder_alpha, is_minor
from ..structure.formula_unit import cluster_atoms, select_formula_unit
from ..structure.geometry import _nearest_pbc_cart, view_rotation
from ..style.palette import atom_r, elem_color, elem_color_light
from ..presets import DEFAULT_STYLE, deep_merge, default_preset, json_safe
from ..static_publication.publication_view import auto_view_dir
from ..static_publication.plot_crystal import _compute_label_positions

# ── New layered modules ──────────────────────────────────────────────
from ..render.display_modes import selected_atoms_for_mode
from ..render.boundary_replicas import expand_boundary_replicas

# Re-export serialization / style helpers from their canonical homes.
from .serialize import (  # noqa: F401
    _to_builtin,
    scene_json,
    scene_metadata,
)
from .style import (  # noqa: F401)
    _resolve_element_color,
    apply_element_colors,
    merge_structure_style,
    rebuild_scene_with_style,
    scene_style,
)

# Legacy aliases for callers that imported the private helpers by name.
# These are now defined in render/display_modes.py and
# render/boundary_replicas.py respectively.
_selected_atoms_for_mode = selected_atoms_for_mode
_expand_boundary_replicas = expand_boundary_replicas


PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.dirname(PACKAGE_DIR)
from ..static_publication import crystal_scene as legacy_scene  # noqa: E402

__all__ = [
    "apply_element_colors",
    "build_scene_from_atoms",
    "build_scene_from_cif",
    "legacy_scene",
    "merge_structure_style",
    "rebuild_scene_with_style",
    "scene_json",
    "scene_metadata",
    "scene_ops",
    "scene_style",
    "_expand_boundary_replicas",
    "_selected_atoms_for_mode",
    "_to_builtin",
]


def scene_ops():
    return SimpleNamespace(
        parse_asu=parse_asu,
        select_formula_unit=select_formula_unit,
        find_bonds=find_bonds,
        auto_view_dir=auto_view_dir,
        view_rotation=view_rotation,
        disorder_alpha=disorder_alpha,
        is_minor=is_minor,
        elem_color=elem_color,
        elem_color_light=elem_color_light,
        atom_r=atom_r,
        compute_label_positions=_compute_label_positions,
    )


def _bond_endpoints(ai, aj, cell, display_mode: str):
    start = np.array(ai["cart"], dtype=float)
    if display_mode in ("formula_unit", "cluster") or (ai.get("_unwrapped") and aj.get("_unwrapped")):
        end = np.array(aj["cart"], dtype=float)
    else:
        end = np.array(_nearest_pbc_cart(ai["cart"], aj["cart"], cell), dtype=float)
    return start, end


def build_scene_from_atoms(
    *,
    name: str,
    title: str,
    atoms,
    cell,
    M,
    R,
    show_hydrogen: bool = False,
    preset: Optional[Dict[str, Any]] = None,
    display_mode: str = "formula_unit",
    ops=None,
    formula_unit_atoms=None,
    unwrapped_atoms=None,
) -> Dict[str, Any]:
    ops = scene_ops() if ops is None else ops
    preset = default_preset() if preset is None else preset
    style = deep_merge(DEFAULT_STYLE, preset.get("style"))
    entry = preset.get("structures", {}).get(name, {})
    style = deep_merge(style, entry.get("style"))
    show_h = bool(show_hydrogen) or bool(entry.get("show_hydrogen", style.get("show_hydrogen", False)))

    sel_atoms = selected_atoms_for_mode(
        ops,
        atoms,
        M,
        cell,
        display_mode=display_mode,
        formula_unit_atoms=formula_unit_atoms,
        unwrapped_atoms=unwrapped_atoms,
    )
    draw_atoms = [dict(atom) for atom in sel_atoms if show_h or atom["elem"] != "H"]

    view_x = np.array(R[0], dtype=float)
    view_y = np.array(R[1], dtype=float)
    view_z = np.array(R[2], dtype=float)

    if draw_atoms:
        depths = np.array([atom["cart"] @ view_z for atom in draw_atoms], dtype=float)
        z_min, z_max = depths.min(), depths.max()
        z_span = max(z_max - z_min, 1e-6)
        for atom, depth in zip(draw_atoms, depths):
            atom["_depth_t"] = float((depth - z_min) / z_span)
            atom["is_minor"] = atom_is_minor(atom)
            atom["disorder_alpha"] = float(ops.disorder_alpha(atom))
            atom["color"] = ops.elem_color(atom["elem"])
            atom["color_light"] = ops.elem_color_light(atom["elem"])
            atom["atom_radius"] = float(ops.atom_r(atom["elem"]))

    effective_cell = None if display_mode == "cluster" else cell
    bond_pairs = ops.find_bonds(draw_atoms, cell=effective_cell)
    bonds = []
    for i, j in bond_pairs:
        ai = draw_atoms[i]
        aj = draw_atoms[j]
        if bonds_conflict(ai, aj):
            continue
        start, end = _bond_endpoints(ai, aj, cell, display_mode=display_mode)
        bonds.append(
            {
                "i": i,
                "j": j,
                "start": start.copy(),
                "end": end.copy(),
                "color_i": ai["color"],
                "color_j": aj["color"],
                "alpha_i": ai["disorder_alpha"],
                "alpha_j": aj["disorder_alpha"],
                "is_minor": bond_is_minor(ai, aj),
                "depth_t": float((ai["_depth_t"] + aj["_depth_t"]) / 2.0),
            }
        )

    label_items = legacy_scene._label_payload(ops, draw_atoms, view_x, view_y, view_z)
    bounds = legacy_scene._compute_bounds(
        draw_atoms or sel_atoms,
        view_x,
        view_y,
        view_z,
        atom_scale=float(style.get("atom_scale", 1.0)),
    )
    camera = entry.get("camera") or legacy_scene._camera_from_bounds(bounds, view_y, view_z)

    M_arr = np.asarray(M, dtype=float)
    projected_axes = [
        (float(M_arr[i] @ view_x), float(M_arr[i] @ view_y))
        for i in range(3)
    ]
    axis_labels = list(style.get("axes_labels") or ["a", "b", "c"])[:3]

    scene = {
        "name": name,
        "title": title,
        "cell": cell,
        "M": M,
        "R": np.array(R, dtype=float),
        "view_x": view_x,
        "view_y": view_y,
        "view_z": view_z,
        "selected_atoms": sel_atoms,
        "draw_atoms": draw_atoms,
        "bonds": bonds,
        "label_items": label_items,
        "bounds": bounds,
        "camera": camera,
        "style": style,
        "show_hydrogen": show_h,
        "has_minor": any(bool(atom["is_minor"]) for atom in draw_atoms),
        "preset_entry": entry,
        "display_mode": display_mode,
        "projected_axes": projected_axes,
        "axis_labels": axis_labels,
    }
    apply_element_colors(
        scene,
        style.get("element_colors"),
        style.get("element_colors_light"),
    )
    return scene


def build_scene_from_cif(
    *,
    name: str,
    cif_path: str,
    title: str,
    preset: Optional[Dict[str, Any]] = None,
    show_hydrogen: bool = False,
    display_mode: str = "formula_unit",
    ops=None,
) -> Dict[str, Any]:
    ops = scene_ops() if ops is None else ops
    preset = default_preset() if preset is None else preset
    atoms, cell, legacy_M = ops.parse_asu(cif_path)
    M = np.asarray(legacy_M, dtype=float).T
    view_dir, up = legacy_scene._resolve_view(ops, name, atoms, legacy_M, cell, preset)
    R = ops.view_rotation(view_dir, up)
    formula_unit_atoms = None
    if display_mode == "formula_unit":
        from ..structure import molcrys_bridge
        formula_unit_atoms = molcrys_bridge.select_formula_unit(atoms, M)
    scene = build_scene_from_atoms(
        name=name,
        title=title,
        atoms=atoms,
        cell=cell,
        M=M,
        R=R,
        preset=preset,
        show_hydrogen=show_hydrogen,
        display_mode=display_mode,
        ops=ops,
        formula_unit_atoms=formula_unit_atoms,
        unwrapped_atoms=None,
    )
    scene["cif_path"] = cif_path
    scene["view_direction"] = np.array(view_dir, dtype=float)
    scene["up"] = np.array(up, dtype=float)
    return scene
