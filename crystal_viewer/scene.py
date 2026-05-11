from __future__ import annotations

import copy
import os
from typing import Any, Dict, Optional

import numpy as np

from .presets import DEFAULT_STYLE, deep_merge, default_preset, json_safe


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.path.dirname(PACKAGE_DIR)
from .legacy import crystal_scene as legacy_scene  # noqa: E402
from .legacy import plot_crystal as pc  # noqa: E402


def scene_ops():
    return pc._scene_ops()


def _resolve_element_color(elem: str, base: str, overrides: Dict[str, str]) -> str:
    """Return the publication-style colour for ``elem``. ``overrides`` wins
    over the vendored palette so figures can add elements (e.g. I, Na, Rb)
    that aren't in the default table, or re-skin defaults."""
    if not overrides:
        return base
    override = overrides.get(elem)
    return override if override else base


def apply_element_colors(
    scene: Dict[str, Any],
    element_colors: Optional[Dict[str, str]] = None,
    element_colors_light: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Apply per-element hex-colour overrides to every atom and bond in an
    already-built scene. Useful when a caller wants to reuse the default
    scene-building pipeline but skin elements specially for a publication
    figure (e.g. colour I purple, Na yellow).

    Mutates ``scene`` in place and returns the same object for chaining;
    this function never returns a fresh scene. ``None`` or empty
    overrides are a no-op **except** when ``scene["style"]["monochrome"]``
    is true: in that case the function forces every atom and bond to
    pure black, mirroring the semantics of the ``monochrome`` style key
    everywhere else in the renderer. Callers that want a non-monochrome
    skin should pass ``element_colors`` explicitly and not rely on the
    monochrome short-circuit.
    """
    if scene.get("style", {}).get("monochrome"):
        element_colors = {atom.get("elem", ""): "#000000" for atom in scene.get("draw_atoms", [])}
        element_colors_light = dict(element_colors)
    if not element_colors and not element_colors_light:
        return scene
    ec = element_colors or {}
    ec_light = element_colors_light or {}
    by_index: dict[int, tuple[str, str]] = {}
    for idx, atom in enumerate(scene.get("draw_atoms", [])):
        elem = atom.get("elem", "")
        new_color = _resolve_element_color(elem, atom.get("color", ""), ec)
        new_light = _resolve_element_color(elem, atom.get("color_light", ""), ec_light or ec)
        atom["color"] = new_color
        atom["color_light"] = new_light
        by_index[idx] = (new_color, new_light)
    for bond in scene.get("bonds", []):
        ci = by_index.get(int(bond.get("i", -1)))
        cj = by_index.get(int(bond.get("j", -1)))
        if ci is not None:
            bond["color_i"] = ci[0]
        if cj is not None:
            bond["color_j"] = cj[0]
    return scene


def _to_builtin(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, dict):
        return {key: _to_builtin(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_builtin(item) for item in value]
    return value


def scene_style(scene: Dict[str, Any], override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    style = copy.deepcopy(DEFAULT_STYLE)
    style.update(scene.get("style", {}))
    if override:
        style.update(override)
    return style


def scene_metadata(scene: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": scene["name"],
        "title": scene["title"],
        "has_minor": bool(scene.get("has_minor", False)),
        "atom_count": len(scene.get("draw_atoms", [])),
        "bond_count": len(scene.get("bonds", [])),
        "cif_path": scene.get("cif_path"),
    }


def scene_json(scene: Dict[str, Any]) -> Dict[str, Any]:
    payload = {}
    for key, value in scene.items():
        if str(key).startswith("_"):
            continue
        if key == "cell":
            payload[key] = {
                "a": float(value.a),
                "b": float(value.b),
                "c": float(value.c),
                "alpha": float(value.alpha),
                "beta": float(value.beta),
                "gamma": float(value.gamma),
                "volume": float(value.volume),
            }
        else:
            payload[key] = _to_builtin(value)
    return payload


def rebuild_scene_with_style(scene: Dict[str, Any], style: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(scene)
    updated["style"] = scene_style(scene, style)
    return updated


def _asymmetric_unit_atoms(atoms):
    selected = []
    seen = set()
    for atom in atoms:
        key = (
            atom.get("label"),
            atom.get("elem"),
            atom.get("dg", "").strip(),
            atom.get("da", "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(dict(atom))
    return selected


def _continuous_components(ops: Any, atoms, M, cell):
    atoms_out = [dict(atom) for atom in atoms]
    bond_pairs = ops.find_bonds(atoms_out, cell=cell)
    clusters = pc.cluster_atoms(atoms_out, bonds=bond_pairs)
    ordered = [sorted(idxs) for _, idxs in sorted(clusters.items(), key=lambda item: min(item[1]))]
    for idxs in ordered:
        atoms_out = pc.assemble_component_p1(atoms_out, idxs, bond_pairs, M)
    return atoms_out, ordered


def _best_component_shift_frac(component_atoms) -> np.ndarray:
    fracs = np.array([atom["frac"] for atom in component_atoms], dtype=float)
    shifts = np.array(
        np.meshgrid(
            np.arange(-2, 3, dtype=float),
            np.arange(-2, 3, dtype=float),
            np.arange(-2, 3, dtype=float),
            indexing="ij",
        )
    ).reshape(3, -1).T
    shifted = fracs[None, :, :] + shifts[:, None, :]
    lower = np.clip(-shifted, 0.0, None)
    upper = np.clip(shifted - 1.0, 0.0, None)
    outside_penalty = np.sum(lower * lower + upper * upper, axis=(1, 2))
    center_penalty = np.linalg.norm(shifted.mean(axis=1) - 0.5, axis=1)
    scores = outside_penalty * 50.0 + center_penalty
    return shifts[int(np.argmin(scores))]


def _translate_component_frac(atoms, idxs, shift_frac, M):
    shift_frac = np.array(shift_frac, dtype=float)
    shift_cart = M @ shift_frac
    translated = [dict(atom) for atom in atoms]
    for idx in idxs:
        translated[idx]["frac"] = np.array(translated[idx]["frac"], dtype=float) + shift_frac
        translated[idx]["cart"] = np.array(translated[idx]["cart"], dtype=float) + shift_cart
    return translated


def _whole_components_in_box(ops: Any, atoms, M, cell):
    atoms_out, components = _continuous_components(ops, atoms, M, cell)
    for idxs in components:
        component_atoms = [atoms_out[idx] for idx in idxs]
        shift_frac = _best_component_shift_frac(component_atoms)
        atoms_out = _translate_component_frac(atoms_out, idxs, shift_frac, M)
    return atoms_out


def _selected_atoms_for_mode(ops: Any, atoms, M, cell, display_mode: str, formula_unit_atoms=None, unwrapped_atoms=None):
    continuous_atoms = unwrapped_atoms if unwrapped_atoms else atoms
    if display_mode == "unit_cell":
        # Mirror VESTA's "unit cell" view: any atom on a face / edge /
        # corner of the cell gets drawn at every equivalent boundary so
        # the visual cell looks closed. Without this, an O sitting at
        # frac=(0,0,0) appears only at one corner and the polyhedron
        # around it on the opposite side of the cell is missing one
        # ligand.
        base = [dict(atom) for atom in continuous_atoms]
        return _expand_boundary_replicas(base, M)
    if display_mode == "asymmetric_unit":
        return _asymmetric_unit_atoms(continuous_atoms)
    if display_mode == "cluster":
        # Molecular cluster / isolated fragment: show every atom as parsed,
        # with no formula-unit trimming or periodic-image reassembly. Bonds
        # are detected directly from the stored Cartesian coordinates.
        return [dict(atom) for atom in atoms]
    # formula_unit: defer to MolCrysKit so the per-species counts come from
    # the cell composition / GCD rather than the legacy ``max_count=4``
    # heuristic.  See crystal_viewer/molcrys_bridge.py.
    if formula_unit_atoms is not None:
        return [dict(atom) for atom in formula_unit_atoms]
    from . import molcrys_bridge
    return molcrys_bridge.select_formula_unit(atoms, M)


_BOUNDARY_TOL = 1e-3  # fractional-coordinate tolerance for "on the cell boundary"


def _expand_boundary_replicas(atoms: list[dict[str, Any]], M: Any) -> list[dict[str, Any]]:
    """Add image-replica copies of any atom that sits on a face / edge /
    corner of the unit cell.

    An atom with fractional coordinate ``f`` near 0 is also a valid
    representative at ``f + 1`` (and vice versa). For an atom on a
    corner ``(0, 0, 0)`` this generates 7 additional images ``(1,0,0)
    ... (1,1,1)`` so the rendered cell looks closed -- see VESTA's
    "Show all equivalent atoms" toggle.

    Returns a new list; atoms already inside ``[tol, 1-tol)`` are
    passed through unchanged.
    """
    if not atoms:
        return atoms
    M_arr = np.asarray(M, dtype=float)
    out: list[dict[str, Any]] = []
    for atom in atoms:
        out.append(atom)
        frac = atom.get("frac")
        if frac is None:
            continue
        frac_arr = np.asarray(frac, dtype=float)
        if frac_arr.shape != (3,):
            continue
        # Per-axis "is on a boundary" with sign of the replica shift.
        # Treat both 0 and 1 as boundary; an atom at frac=0.5 is NOT
        # on a face for unit-cell tiling (only frac=0 / 1 are) -- this
        # mirrors VESTA's behaviour.
        shifts: list[tuple[int, int, int]] = []
        per_axis_shifts: list[list[int]] = [[0], [0], [0]]
        for axis in range(3):
            f = float(frac_arr[axis])
            # Strict windows around 0 and 1 only -- unwrapped atoms at
            # frac=1.02 (drawn outside the cell to keep a molecule
            # continuous) must NOT trigger a replica because their true
            # crystallographic site is the home cell, not a special
            # position. ``f`` outside the strict windows is interior or
            # continuation; either way no replica is needed.
            on_zero = -_BOUNDARY_TOL <= f <= _BOUNDARY_TOL
            on_one = 1.0 - _BOUNDARY_TOL <= f <= 1.0 + _BOUNDARY_TOL
            if on_zero:
                per_axis_shifts[axis] = [0, 1]
            elif on_one:
                per_axis_shifts[axis] = [0, -1]
        for sa in per_axis_shifts[0]:
            for sb in per_axis_shifts[1]:
                for sc in per_axis_shifts[2]:
                    if sa == 0 and sb == 0 and sc == 0:
                        continue
                    shifts.append((sa, sb, sc))
        if not shifts:
            continue
        for shift in shifts:
            replica = dict(atom)
            shift_arr = np.array(shift, dtype=float)
            replica["frac"] = frac_arr + shift_arr
            replica["cart"] = np.asarray(atom.get("cart"), dtype=float) + (M_arr @ shift_arr)
            replica["_image_shift"] = shift
            replica["_origin_label"] = atom.get("_origin_label", atom.get("label"))
            replica["_is_boundary_replica"] = True
            out.append(replica)
    return out


def _bond_endpoints(ai, aj, cell, display_mode: str):
    start = np.array(ai["cart"], dtype=float)
    if display_mode in ("formula_unit", "cluster") or (ai.get("_unwrapped") and aj.get("_unwrapped")):
        # Plain Euclidean endpoints. For clusters the atoms are already
        # expressed in Cartesian coordinates with no periodic imaging.
        end = np.array(aj["cart"], dtype=float)
    else:
        end = np.array(pc._nearest_pbc_cart(ai["cart"], aj["cart"], cell), dtype=float)
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
    # Explicit caller intent (UI checkbox / build_bundle_scene kwarg) wins.
    # The preset-level toggles only matter when the caller hasn't already
    # asked for hydrogens, so a freshly-loaded scene with a default-off
    # preset still honours a user click on the "Hydrogens" checkbox.
    show_h = bool(show_hydrogen) or bool(entry.get("show_hydrogen", style.get("show_hydrogen", False)))

    sel_atoms = _selected_atoms_for_mode(
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
            atom["is_minor"] = bool(ops.is_minor(atom))
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
                "is_minor": bool(ai["is_minor"] or aj["is_minor"]),
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

    # Projected axis directions in screen 2D (a, b, c order). Callers that
    # want to draw their own axis triad — e.g. as a matplotlib overlay outside
    # the Plotly render — can consume this directly without re-deriving the
    # camera basis. Entries are (dx, dy) in "screen right / screen up"
    # components, matching ``view_x``/``view_y``.
    M_arr = np.asarray(M, dtype=float)
    projected_axes = [
        (float(M_arr[:, i] @ view_x), float(M_arr[:, i] @ view_y))
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
        # Axis projection exposed so external callers can draw consistent
        # legend-style axis keys without re-deriving the camera basis.
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
    atoms, cell, M = ops.parse_asu(cif_path)
    view_dir, up = legacy_scene._resolve_view(ops, name, atoms, M, cell, preset)
    R = ops.view_rotation(view_dir, up)
    formula_unit_atoms = None
    if display_mode == "formula_unit":
        from . import molcrys_bridge
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


def merge_structure_style(preset: Dict[str, Any], name: str, style: Dict[str, Any]) -> Dict[str, Any]:
    merged = default_preset() if preset is None else copy.deepcopy(preset)
    merged["style"] = deep_merge(merged.get("style", {}), style)
    merged.setdefault("structures", {})
    merged["structures"].setdefault(name, {})
    merged["structures"][name]["style"] = json_safe(style)
    return merged
