from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Iterable, Optional

import numpy as np


DEFAULT_STYLE = {
    "display_mode": "formula_unit",
    "atom_scale": 1.0,
    "bond_radius": 0.15,
    "material": "mesh",
    "style": "ball_stick",
    "disorder": "outline_rings",
    "major_opacity": 1.0,
    "minor_opacity": 0.35,
    "minor_wireframe": False,
    "minor_bond_scale": 0.82,
    "show_labels": False,
    "show_axes": True,
    "show_title": True,
    "show_hydrogen": True,
    "show_unit_cell": True,
    "show_minor_only": False,
    "depth_cue_enabled": False,
    "projection": "perspective",
    "camera_eye_distance": 1.8,
    "background": "#FFFFFF",
    "label_color": "#111111",
    "minor_label_color": "#666666",
    "axis_scale": 0.14,
    "axis_color": "#666666",
    "axis_opacity": 0.72,
    "axes_labels": ["a", "b", "c"],
    # Corner axis-key overlay: a compact triad rendered as Plotly paper-coord
    # annotations so the labels sit cleanly inside a figure corner and can
    # never be clipped by the 3D viewport or a caller's outer axes. The
    # in-app ``show_axes`` checkbox feeds the same overlay (it used to
    # render a 3D cylinder shaft in world space, which foreshortened to a
    # stub on cameras aligned with a lattice vector and cut a long line
    # through the structure on oblique cameras). Set ``show_axis_key`` only
    # when a caller wants the publication-style triad with explicit
    # ``axis_key_*`` paper-coord controls; the in-app default uses the
    # ``axis_scale`` slider instead.
    "show_axis_key": False,
    # Single-anchor triad compass: this is the SHARED tail position
    # for all three lattice arrows (paper coords). Pushed in from the
    # absolute corner so down-pointing arrows still have room for
    # their labels below the tip without clipping past the figure
    # edge. Old row-stacked layout used 0.05,0.07 -- safe for vertical
    # rows but too close to the corner for an omnidirectional triad.
    "axis_key_anchor": [0.10, 0.18],      # shared paper-coord tail
    "axis_key_row_gap": 0.095,            # legacy field, unused by the triad
    "axis_key_arrow_len": 0.085,          # legacy field, unused by the triad
    "axis_key_label_pad": 0.045,          # legacy field, unused by the triad
    "axis_key_pixel_length": 50.0,        # max arrow length in pixels
    "axis_key_label_pixel_offset": 10.0,  # label push past arrow tip
    "axis_key_arrow_head": 3,             # Plotly arrowhead style id
    "axis_key_dot_threshold": 0.05,       # rel-magnitude threshold for dot
    "axis_key_dot_radius_px": 4.0,        # dot radius in pixels
    "axis_key_font_size": 13,             # label font size (points)
    "axis_key_color": "#2F2F2F",
    "axis_key_label_order": ["c", "b", "a"],  # top→bottom stacking order
    "axis_key_italic": True,
    "fast_rendering": False,
    "topology_enabled": False,
    "monochrome": False,
    "ortep_probability": 0.5,
    "ortep_mode": "ortep_axes",
    "ortep_mode_minor": None,
    "ortep_show_principal_axes": True,
    "ortep_axis_color": "#222222",
    "ortep_axis_linewidth": 1.6,
    "ortep_octant_shading": False,
    "ortep_octant_shadow_color": "#000000",
    "ortep_octant_shadow_alpha": 0.18,
    # Hatch shading (classic ORTEP-III): parallel surface arcs on one octant
    # plus three boundary arcs framing it.  Mutually compatible with the
    # solid-fill octant shading above; both can be on at once but normally
    # callers pick one or the other via ``ortep_mode``.
    "ortep_octant_hatching": False,
    "ortep_octant_hatch_color": "#1A1A1A",
    "ortep_octant_hatch_linewidth": 1.4,
    "ortep_octant_hatch_lines": 5,
    "ortep_octant_hatch_arc_pts": 16,
    "ortep_octant_edge_color": "#0F0F0F",
    "ortep_octant_edge_linewidth": 1.9,
    # Silhouette outline (the dark line that gives ORTEP its "open ellipsoid"
    # publication look).  When false the white Mesh3d body merges with a
    # white background; turn this on for any "ortep_hatch" / "ortep_octant"
    # rendering on light backgrounds.
    "ortep_silhouette_outline": False,
    "ortep_silhouette_color": "#1A1A1A",
    "ortep_silhouette_linewidth": 1.4,
    # White (or other) filled billboard disk per atom.  Drawn between the
    # bond strokes and the silhouette outline so bonds don't show through
    # the atom region.  Only meaningful when ``ortep_octant_hatching`` is
    # on (otherwise the Mesh3d body provides the same coverage).
    "ortep_atom_fill": False,
    "ortep_atom_fill_color": "#FFFFFF",
    "ortep_z_lift_fill":    0.04,   # white disk lifted toward camera (Å)
    "ortep_z_lift_hatch":   0.06,   # hatch lifted further (so it sits over the disk)
    "ortep_z_lift_outline": 0.07,   # silhouette lifted most (so it sits on top of all)
    # Force every bond half to a single hex colour.  Empty string keeps the
    # default per-atom split-colour behaviour.  Used by publication ORTEP
    # presets to render bonds as plain black ink without flipping the
    # ``monochrome`` flag (which would also blacken atom fills).
    "force_bond_color": "",
    # Optional hex-colour overrides for elements not in the vendored palette,
    # or to re-skin existing ones for publication figures. The ``elements``
    # dict takes precedence over ``elements_light`` for both primary colour
    # and highlight colour. Keys are element symbols (e.g. ``"I"``, ``"Na"``).
    "element_colors": {},
    "element_colors_light": {},
}

# Global config owns the effective default style. The literal above remains as
# the historical table of record for readers of this file; this live mapping is
# what callers import so config reloads are visible to `dict(DEFAULT_STYLE)`.
from ..config import DEFAULT_STYLE as DEFAULT_STYLE  # noqa: E402,F811

ORTEP_MODES = {
    "ortep_solid": {
        "ortep_show_principal_axes": False,
        "ortep_octant_shading": False,
    },
    "ortep_axes": {
        "ortep_show_principal_axes": True,
        "ortep_octant_shading": False,
    },
    "ortep_octant": {
        "ortep_show_principal_axes": False,
        "ortep_octant_shading": True,
    },
    # Classic ORTEP-III publication look: white open ellipsoid + dark
    # silhouette outline + parallel hatch lines on one octant facing the
    # camera + three boundary arcs framing the hatch.
    "ortep_hatch": {
        "ortep_show_principal_axes": False,
        "ortep_octant_shading": False,
        "ortep_octant_hatching": True,
        "ortep_silhouette_outline": True,
        "ortep_atom_fill": True,
    },
}

MONOCHROME_STYLE = {
    "monochrome": True,
    "label_color": "#000000",
    "element_colors": {},
    "element_colors_light": {},
}

DEFAULT_CATALOG = {
    "DAP-4": {
        "title": "DAP-4  (P1, Z=12)",
        "relative_cif": os.path.join("scripts", "data", "DAP-4.cif"),
    },
}

LOCAL_STATE_DIRNAME = ".local"
LOCAL_PRESET_FILENAME = "crystal_view_preset.json"
LOCAL_CATALOG_FILENAMES = (
    "catalog.local.json",
    os.path.join(LOCAL_STATE_DIRNAME, "catalog.local.json"),
)

DEFAULT_STRUCTURE_PRESETS = {
    "DAP-4": {
        "view_direction": [1.0, 0.0, 0.0],
        "up": [0.0, 0.0, 1.0],
        "show_hydrogen": False,
    },
    "SY": {
        "view_direction": [1.0, 1.0, 1.0],
        "up": [0.0, 0.0, 1.0],
        "show_hydrogen": False,
    },
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    if not override:
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def workspace_root(package_dir: Optional[str] = None) -> str:
    if package_dir is None:
        package_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(package_dir)


def default_preset_path(root_dir: Optional[str] = None) -> str:
    root = workspace_root() if root_dir is None else root_dir
    return os.path.join(root, LOCAL_STATE_DIRNAME, LOCAL_PRESET_FILENAME)


def _resolve_catalog_entry(base_dir: str, entry: Dict[str, Any]) -> Optional[Dict[str, str]]:
    cif_path = entry.get("cif_path")
    if not cif_path:
        return None
    resolved_path = cif_path if os.path.isabs(cif_path) else os.path.normpath(os.path.join(base_dir, cif_path))
    if not os.path.exists(resolved_path):
        return None
    title = str(entry.get("title") or os.path.splitext(os.path.basename(resolved_path))[0])
    return {
        "title": title,
        "cif_path": resolved_path,
    }


def _load_local_catalog(root: str) -> Dict[str, Dict[str, str]]:
    catalog: Dict[str, Dict[str, str]] = {}
    for relative_path in LOCAL_CATALOG_FILENAMES:
        config_path = os.path.join(root, relative_path)
        if not os.path.exists(config_path):
            continue
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        raw_entries = raw.get("structures", raw) if isinstance(raw, dict) else {}
        if not isinstance(raw_entries, dict):
            continue
        for name, entry in raw_entries.items():
            if not isinstance(entry, dict):
                continue
            resolved = _resolve_catalog_entry(os.path.dirname(config_path), entry)
            if resolved:
                catalog[str(name)] = resolved
    return catalog


def get_default_catalog(root_dir: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    root = workspace_root() if root_dir is None else root_dir
    catalog = _load_local_catalog(root)
    for name, entry in DEFAULT_CATALOG.items():
        if name in catalog:
            continue
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
        "structures": copy.deepcopy(DEFAULT_STRUCTURE_PRESETS),
    }


def load_preset(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return default_preset()
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return _deep_merge(default_preset(), raw)


def save_preset(path: str, preset: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_json_safe(preset), handle, indent=2, ensure_ascii=False)


def scene_from_camera(position: Iterable[float], focal_point: Iterable[float], up: Iterable[float]):
    position = np.array(position, dtype=float)
    focal_point = np.array(focal_point, dtype=float)
    up = np.array(up, dtype=float)
    view_dir = position - focal_point
    if np.linalg.norm(view_dir) < 1e-8:
        view_dir = np.array([0.0, 0.0, 1.0], dtype=float)
    view_dir /= np.linalg.norm(view_dir)
    if np.linalg.norm(up) < 1e-8:
        up = np.array([0.0, 1.0, 0.0], dtype=float)
    up /= np.linalg.norm(up)
    return view_dir, up


def scene_to_preset_entry(scene: Dict[str, Any], camera=None, style=None) -> Dict[str, Any]:
    entry = {
        "camera": _json_safe(camera or scene.get("camera", {})),
        "show_hydrogen": bool(scene.get("show_hydrogen", False)),
    }
    if style:
        entry["style"] = _json_safe(style)
    return entry


def json_safe(value: Any) -> Any:
    return _json_safe(value)


def deep_merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return _deep_merge(base, override)
