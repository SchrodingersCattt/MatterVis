from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Iterable, Optional

import numpy as np

try:
    from ..disorder import atom_is_minor, bond_is_minor
except ImportError:  # pragma: no cover - allows direct script execution
    from crystal_viewer.disorder import atom_is_minor, bond_is_minor  # type: ignore


DEFAULT_STYLE = {
    "atom_scale": 1.0,
    "bond_radius": 0.16,
    "major_opacity": 1.0,
    "minor_opacity": 0.35,
    "minor_wireframe": False,
    "minor_bond_scale": 0.82,
    "show_labels": True,
    "show_axes": True,
    "show_hydrogen": False,
    "show_unit_cell": False,
    "show_minor_only": False,
    "depth_cue_enabled": False,
    "background": "#FFFFFF",
    "axis_scale": 0.14,
    "axis_color": "#666666",
    "axis_opacity": 0.72,
}

DEFAULT_CATALOG = {
    "DAP-4": {
        "title": "DAP-4  (P1, Z=12)",
        "relative_cif": os.path.join("scripts", "data", "DAP-4.cif"),
    },
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    if not override:
        return out
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def workspace_root(figures_dir: Optional[str] = None) -> str:
    if figures_dir is None:
        figures_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(figures_dir))


def get_default_catalog(root_dir: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    root = workspace_root() if root_dir is None else root_dir
    catalog = {}
    for name, entry in DEFAULT_CATALOG.items():
        cif_path = os.path.normpath(os.path.join(root, entry["relative_cif"]))
        if not os.path.exists(cif_path):
            continue
        catalog[name] = {
            "title": entry["title"],
            "cif_path": cif_path,
        }
    return catalog


def default_preset() -> Dict[str, Any]:
    return {
        "version": 1,
        "style": copy.deepcopy(DEFAULT_STYLE),
        "structures": {},
    }


def load_preset(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return default_preset()
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return _deep_merge(default_preset(), raw)


def save_preset(path: str, preset: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_json_safe(preset), f, indent=2, ensure_ascii=False)


def scene_from_camera(position: Iterable[float], focal_point: Iterable[float], up: Iterable[float]):
    position = np.array(position, dtype=float)
    focal_point = np.array(focal_point, dtype=float)
    up = np.array(up, dtype=float)
    view_dir = position - focal_point
    if np.linalg.norm(view_dir) < 1e-8:
        view_dir = np.array([0.0, 0.0, 1.0])
    view_dir /= np.linalg.norm(view_dir)
    if np.linalg.norm(up) < 1e-8:
        up = np.array([0.0, 1.0, 0.0])
    up /= np.linalg.norm(up)
    return view_dir, up


def _structure_entry(preset: Dict[str, Any], name: str) -> Dict[str, Any]:
    return preset.get("structures", {}).get(name, {})


def _resolve_view(ops: Any, name: str, atoms, M, cell, preset: Dict[str, Any]):
    entry = _structure_entry(preset, name)
    camera = entry.get("camera", {})
    if camera.get("position") and camera.get("focal_point") and camera.get("up"):
        return scene_from_camera(camera["position"], camera["focal_point"], camera["up"])
    if entry.get("view_direction"):
        up = entry.get("up", [0.0, 1.0, 0.0])
        return np.array(entry["view_direction"], dtype=float), np.array(up, dtype=float)
    return ops.auto_view_dir(atoms, M, cell, compound_name=name)


def _camera_from_bounds(bounds, view_y, view_z):
    center = np.array(bounds["center"], dtype=float)
    span = max(bounds["ranges"]) if bounds["ranges"] else 1.0
    distance = max(8.0, span * 2.8)
    position = center + distance * np.array(view_z, dtype=float)
    focal_point = center
    up = np.array(view_y, dtype=float)
    return {
        "position": position.tolist(),
        "focal_point": focal_point.tolist(),
        "up": up.tolist(),
    }


def _compute_bounds(atoms, view_x, view_y, view_z, *, atom_scale=1.0, extra_pad=0.35):
    """Compute the world-space bounds used to size the Matplotlib Axes3D
    viewport. The box is inflated by each atom's visual radius so large
    elements (e.g. Cl, I, Br) are not clipped at the panel edge.

    Parameters
    ----------
    atoms
        Sequence of atom dicts carrying ``cart`` and optionally ``atom_radius``
        (set by ``build_scene_from_atoms``).
    view_x, view_y, view_z
        Orthonormal screen-space basis vectors.
    atom_scale
        Multiplies each atom's ``atom_radius`` when computing padding. Matches
        ``style["atom_scale"]`` so the bounds stay correct when callers scale
        the rendered spheres.
    extra_pad
        Extra padding in Å added after the radius-aware inflation, for visual
        breathing room around the structure. Kept at 0.35 Å to preserve the
        pre-existing Axes3D framing.
    """
    empty_bounds = {
        "center": [0.0, 0.0, 0.0],
        "ranges": [1.0, 1.0, 1.0],
        "mins": [0.0, 0.0, 0.0],
        "maxs": [1.0, 1.0, 1.0],
        "screen_ranges": [1.0, 1.0, 1.0],
    }
    if not atoms:
        return empty_bounds
    carts = np.array([at["cart"] for at in atoms], dtype=float)
    radii = np.array(
        [max(float(at.get("atom_radius", 0.18)), 0.05) for at in atoms],
        dtype=float,
    ) * float(atom_scale)

    mins = (carts - radii[:, None]).min(axis=0)
    maxs = (carts + radii[:, None]).max(axis=0)

    sx = carts @ view_x
    sy = carts @ view_y
    sz = carts @ view_z
    sx_min = float(sx.min() - radii.max())
    sx_max = float(sx.max() + radii.max())
    sy_min = float(sy.min() - radii.max())
    sy_max = float(sy.max() + radii.max())
    sz_min = float(sz.min() - radii.max())
    sz_max = float(sz.max() + radii.max())
    pad_s = float(extra_pad)
    screen_ranges = [
        (sx_max - sx_min) + 2.0 * pad_s,
        (sy_max - sy_min) + 2.0 * pad_s,
        (sz_max - sz_min) + 2.0 * pad_s,
    ]
    return {
        "center": carts.mean(axis=0).tolist(),
        "ranges": (maxs - mins).tolist(),
        "mins": mins.tolist(),
        "maxs": maxs.tolist(),
        "screen_ranges": screen_ranges,
    }


def _label_payload(ops: Any, draw_atoms, view_x, view_y, view_z):
    label_atoms_all = [at for at in draw_atoms if at["elem"] != "H"]
    seen_labels = {}
    for at in label_atoms_all:
        sy_val = float(at["cart"] @ view_y)
        lbl = at["label"]
        cur_minor = atom_is_minor(at)
        if lbl not in seen_labels:
            seen_labels[lbl] = (at, sy_val, cur_minor)
            continue
        prev_at, prev_sy, prev_minor = seen_labels[lbl]
        if prev_minor and not cur_minor:
            seen_labels[lbl] = (at, sy_val, cur_minor)
            continue
        if (not prev_minor) and cur_minor:
            continue
        if sy_val > prev_sy:
            seen_labels[lbl] = (at, sy_val, cur_minor)
    label_atoms = [v[0] for v in seen_labels.values()]
    try:
        label_positions = ops.compute_label_positions(
            label_atoms, view_x, view_y,
            base_offset=0.46, all_atoms=draw_atoms,
        )
    except TypeError:
        label_positions = ops.compute_label_positions(
            label_atoms, view_x, view_y, base_offset=0.46,
        )
    label_items = []
    for at, lpos_screen in zip(label_atoms, label_positions):
        sz = float(at["cart"] @ view_z)
        lpos_3d = lpos_screen + sz * view_z
        label_items.append({
            "atom_cart": at["cart"].copy(),
            "label_cart": lpos_3d.copy(),
            "text": at["label"],
            "is_minor": atom_is_minor(at),
        })
    return label_items


def build_scene_from_atoms(
    ops: Any,
    *,
    name: str,
    title: str,
    atoms,
    cell,
    M,
    R,
    show_hydrogen: bool = False,
    preset: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    preset = default_preset() if preset is None else preset
    style = _deep_merge(DEFAULT_STYLE, preset.get("style"))
    entry = _structure_entry(preset, name)
    style = _deep_merge(style, entry.get("style"))
    show_h = bool(entry.get("show_hydrogen", style.get("show_hydrogen", show_hydrogen)))

    atoms_out, sel_idxs = ops.select_formula_unit(atoms, M, cell)
    sel_atoms = [atoms_out[i] for i in sel_idxs]
    draw_atoms = [dict(at) for at in sel_atoms if show_h or at["elem"] != "H"]

    view_x = np.array(R[0], dtype=float)
    view_y = np.array(R[1], dtype=float)
    view_z = np.array(R[2], dtype=float)

    if draw_atoms:
        depths = np.array([at["cart"] @ view_z for at in draw_atoms], dtype=float)
        z_min, z_max = depths.min(), depths.max()
        z_span = max(z_max - z_min, 1e-6)
        for at, d in zip(draw_atoms, depths):
            at["_depth_t"] = float((d - z_min) / z_span)
            at["is_minor"] = atom_is_minor(at)
            at["disorder_alpha"] = float(ops.disorder_alpha(at))
            at["color"] = ops.elem_color(at["elem"])
            at["color_light"] = ops.elem_color_light(at["elem"])
            at["atom_radius"] = float(ops.atom_r(at["elem"]))

    bond_pairs = ops.find_bonds(draw_atoms)
    bonds = []
    for i, j in bond_pairs:
        ai = draw_atoms[i]
        aj = draw_atoms[j]
        bonds.append({
            "i": i,
            "j": j,
            "start": ai["cart"].copy(),
            "end": aj["cart"].copy(),
            "color_i": ai["color"],
            "color_j": aj["color"],
            "alpha_i": ai["disorder_alpha"],
            "alpha_j": aj["disorder_alpha"],
            # Derived only from loader-authored atom ``_is_minor`` flags.
            "is_minor": bond_is_minor(ai, aj),
            "depth_t": float((ai["_depth_t"] + aj["_depth_t"]) / 2.0),
        })

    label_items = _label_payload(ops, draw_atoms, view_x, view_y, view_z)
    bounds = _compute_bounds(
        draw_atoms or sel_atoms,
        view_x,
        view_y,
        view_z,
        atom_scale=float(style.get("atom_scale", 1.0)),
    )
    camera = entry.get("camera") or _camera_from_bounds(bounds, view_y, view_z)

    return {
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
        "has_minor": any(bool(at["is_minor"]) for at in draw_atoms),
        "preset_entry": entry,
    }


def build_structure_scene(
    ops: Any,
    *,
    name: str,
    cif_path: str,
    title: str,
    preset: Optional[Dict[str, Any]] = None,
    show_hydrogen: bool = False,
) -> Dict[str, Any]:
    preset = default_preset() if preset is None else preset
    atoms, cell, M = ops.parse_asu(cif_path)
    view_dir, up = _resolve_view(ops, name, atoms, M, cell, preset)
    R = ops.view_rotation(view_dir, up)
    scene = build_scene_from_atoms(
        ops,
        name=name,
        title=title,
        atoms=atoms,
        cell=cell,
        M=M,
        R=R,
        show_hydrogen=show_hydrogen,
        preset=preset,
    )
    scene["cif_path"] = cif_path
    scene["view_direction"] = np.array(view_dir, dtype=float)
    scene["up"] = np.array(up, dtype=float)
    return scene


def build_default_scenes(
    ops: Any,
    *,
    root_dir: Optional[str] = None,
    preset: Optional[Dict[str, Any]] = None,
    names: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    preset = default_preset() if preset is None else preset
    catalog = get_default_catalog(root_dir=root_dir)
    selected_names = list(names) if names is not None else list(catalog.keys())
    scenes = {}
    for name in selected_names:
        if name not in catalog:
            continue
        meta = catalog[name]
        scenes[name] = build_structure_scene(
            ops,
            name=name,
            cif_path=meta["cif_path"],
            title=meta["title"],
            preset=preset,
        )
    return scenes


def scene_to_preset_entry(scene: Dict[str, Any], camera=None, style=None) -> Dict[str, Any]:
    entry = {
        "camera": _json_safe(camera or scene.get("camera", {})),
        "show_hydrogen": bool(scene.get("show_hydrogen", False)),
    }
    if style:
        entry["style"] = _json_safe(style)
    return entry
