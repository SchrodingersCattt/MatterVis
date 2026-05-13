from __future__ import annotations

import math
from typing import Dict, Iterable, Tuple

import numpy as np
import plotly.graph_objects as go

from . import perf_log
from .presets import ORTEP_MODES


MATERIAL_DISPATCH = {"flat": "_scatter_atom_base", "mesh": "_mesh3d_atom_base"}
STYLE_DISPATCH = {
    "ball": "_sphere_geom",
    "ball_stick": "_sphere_geom",
    "stick": "_stick_only_geom",
    "ortep": "_ellipsoid_geom",
    "wireframe": "_ring_geom",
}
DISORDER_DISPATCH = {
    "opacity": "_disorder_alpha",
    "dashed_bonds": "_disorder_dash",
    "outline_rings": "_disorder_outline",
    "color_shift": "_disorder_color",
    "none": "_disorder_noop",
}


def validate_style_schema(style: dict) -> dict:
    material = str(style.get("material", "mesh"))
    render_style = str(style.get("style", "ball_stick"))
    disorder = str(style.get("disorder", "outline_rings"))
    ortep_mode = style.get("ortep_mode")
    ortep_mode_minor = style.get("ortep_mode_minor")
    projection = str(style.get("projection", "perspective"))
    if material not in MATERIAL_DISPATCH:
        raise ValueError(f"unknown material: {material}")
    if render_style not in STYLE_DISPATCH:
        raise ValueError(f"unknown style: {render_style}")
    if disorder not in DISORDER_DISPATCH:
        raise ValueError(f"unknown disorder mode: {disorder}")
    if ortep_mode is not None and str(ortep_mode) not in ORTEP_MODES:
        raise ValueError(f"unknown ORTEP mode: {ortep_mode}")
    if ortep_mode_minor is not None and str(ortep_mode_minor) not in ORTEP_MODES:
        raise ValueError(f"unknown minor ORTEP mode: {ortep_mode_minor}")
    if projection not in ("perspective", "orthographic"):
        raise ValueError(f"unknown projection: {projection}")
    normalized = dict(style)
    normalized["material"] = material
    normalized["style"] = render_style
    normalized["disorder"] = disorder
    normalized["projection"] = projection
    normalized["camera_eye_distance"] = float(normalized.get("camera_eye_distance", 1.8))
    if ortep_mode is not None:
        normalized["ortep_mode"] = str(ortep_mode)
        normalized.update(ORTEP_MODES[normalized["ortep_mode"]])
    if ortep_mode_minor is not None:
        normalized["ortep_mode_minor"] = str(ortep_mode_minor)
    normalized["fast_rendering"] = bool(normalized.get("fast_rendering", False)) or material == "flat"
    normalized["minor_wireframe"] = bool(normalized.get("minor_wireframe", False)) or disorder == "outline_rings"
    return normalized


def _minor_opacity_for(style: dict, is_minor: bool) -> float:
    if not is_minor:
        return float(style.get("major_opacity", 1.0))
    fade = (
        style.get("disorder") == "opacity"
        or bool(style.get("force_minor_fade", False))
    )
    if fade:
        return max(0.05, float(style.get("minor_opacity", 0.35)))
    return 1.0


def _stamp_trace(
    trace,
    *,
    role: str,
    is_minor: bool | None = None,
    hide_on_minor_only: bool = False,
    visible: bool | None = None,
):
    meta = dict(getattr(trace, "meta", None) or {})
    meta["mv_role"] = role
    if is_minor is not None:
        meta["mv_minor"] = bool(is_minor)
    if hide_on_minor_only:
        meta["mv_hide_on_minor_only"] = True
    trace.meta = meta
    if visible is not None:
        trace.visible = bool(visible)
    return trace


def _style_color(color: str, style: dict) -> str:
    """Apply the legacy ``monochrome`` flag.

    When ``style["atom_groups"]`` is non-empty the monochrome flag is
    treated as inert: atom_groups is the single source of truth and
    double-applying ``monochrome`` would surprise users who set up an
    explicit colour rule but expected unmatched atoms to keep their
    element palette. A backend caller that wants "everything black"
    should add ``{"selector": {"all": True}, "color": "#000000"}`` to
    atom_groups -- the migration in ``ViewerBackend.normalize_state``
    does this automatically when an old preset was loaded with
    ``monochrome=True``.
    """
    if style.get("atom_groups"):
        return color
    return "#000000" if style.get("monochrome", False) else color


def _atom_render_color(atom: dict, style: dict, *, light: bool = False) -> str:
    """Resolve an atom's effective render colour after Phase 2
    atom_groups overrides.

    - ``atom["_render_color"]`` (or ``_render_color_light`` for the
      minor / light path) wins when set by a matching group rule.
    - Otherwise we fall back to the element-palette colour passed
      through :func:`_style_color`. The legacy ``monochrome`` flag
      only takes effect when no atom_groups are set on the scene.
    """
    field = "_render_color_light" if light else "_render_color"
    override = atom.get(field)
    if override:
        return str(override)
    base = atom.get("color_light" if light else "color", "#888888")
    return _style_color(base, style)


def _atom_render_visible(atom: dict) -> bool:
    return bool(atom.get("_render_visible", True))


def _atom_render_opacity_scale(atom: dict) -> float:
    try:
        return max(0.0, min(1.0, float(atom.get("_render_opacity_scale", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def _atom_effective_opacity(atom: dict, style: dict) -> float:
    """Resolve an atom's final opacity after Phase 2 atom_groups overrides.

    Replace semantics: when an atom_group rule supplies an explicit
    opacity (i.e. ``_render_opacity_scale`` was set to anything other
    than the default 1.0), we use that value directly and IGNORE the
    disorder/minor fade for this atom. Otherwise we fall back to the
    legacy per-style fade (``_minor_opacity_for``).

    Stacking semantics (multiplicative) caused minor + group=0.5 atoms
    to drift to ~0.18, which read as "disappearing" rather than
    "halved" -- and a user setting opacity=0 expects an invisible atom,
    not "0 × something".
    """
    is_minor = bool(atom.get("is_minor", False))
    base = _minor_opacity_for(style, is_minor)
    scale = atom.get("_render_opacity_scale", 1.0)
    try:
        scale_f = max(0.0, min(1.0, float(scale)))
    except (TypeError, ValueError):
        scale_f = 1.0
    if scale_f >= 0.999:
        return base
    return scale_f


def _atom_opacity_group_id(atom: dict) -> str | None:
    group_id = atom.get("_render_opacity_group_id")
    if group_id is None:
        return None
    text = str(group_id)
    return text or None


def _bond_opacity_group_id(bond: dict) -> str | None:
    group_id = bond.get("_render_opacity_group_id")
    if group_id is None:
        return None
    text = str(group_id)
    return text or None


def _latency_meta(role: str, *, is_minor: bool | None = None, opacity_group: str | None = None) -> dict:
    meta = {"mv_role": role}
    if is_minor is not None:
        meta["mv_minor"] = bool(is_minor)
    if opacity_group:
        meta["mv_opacity_group"] = str(opacity_group)
    return meta


def _annotate_trace(trace, role: str, *, is_minor: bool | None = None, opacity_group: str | None = None):
    if trace is not None:
        trace.update(meta=_latency_meta(role, is_minor=is_minor, opacity_group=opacity_group))
    return trace


def _style_trace_dicts(trace_dicts: list[dict], style: dict) -> list[dict]:
    """Apply style-only visibility/opacity to cached trace dictionaries.

    The geometry cache intentionally ignores controls such as
    ``show_minor_only`` and opacity sliders. Those controls are cheap
    trace-property edits, so replay cached vertex arrays and stamp the
    current visible/opacity values onto shallow copies.
    """
    show_minor_only = bool(style.get("show_minor_only", False))
    show_labels = bool(style.get("show_labels", True))
    show_axes = bool(style.get("show_axes", True))
    show_unit_cell = bool(style.get("show_unit_cell", False))
    atom_group_opacity = {
        str(group.get("id") or ""): float(group.get("opacity"))
        for group in (style.get("atom_groups") or [])
        if group.get("id") and group.get("opacity") is not None
    }
    bond_group_opacity = {
        str(group.get("id") or ""): float(group.get("opacity"))
        for group in (style.get("bond_groups") or [])
        if group.get("id") and group.get("opacity") is not None
    }
    out: list[dict] = []
    for trace in trace_dicts:
        copied = dict(trace)
        meta = copied.get("meta") if isinstance(copied.get("meta"), dict) else {}
        role = meta.get("mv_role")
        is_minor = bool(meta.get("mv_minor", False))
        if role == "labels":
            copied["visible"] = show_labels and (not show_minor_only or is_minor)
        elif role == "axes":
            copied["visible"] = show_axes
        elif role == "unit_cell":
            copied["visible"] = show_unit_cell
        elif show_minor_only and role in {"atom", "bond", "atom_selection", "bond_selection"} and not is_minor:
            copied["visible"] = False
        elif role in {"atom", "bond", "atom_selection", "bond_selection"}:
            copied["visible"] = True
        group_id = meta.get("mv_opacity_group")
        if role == "atom":
            opacity = atom_group_opacity.get(str(group_id), _minor_opacity_for(style, is_minor))
            if copied.get("type") == "scatter3d":
                marker = dict(copied.get("marker") or {})
                marker["opacity"] = opacity
                copied["marker"] = marker
            else:
                copied["opacity"] = opacity
        elif role == "bond":
            opacity = bond_group_opacity.get(str(group_id), _minor_opacity_for(style, is_minor))
            copied["opacity"] = opacity
        out.append(copied)
    return out


def _normalize(vec: Iterable[float], fallback: Iterable[float]) -> np.ndarray:
    arr = np.array(list(vec), dtype=float)
    if arr.shape != (3,) or np.linalg.norm(arr) < 1e-8:
        arr = np.array(list(fallback), dtype=float)
    norm = np.linalg.norm(arr)
    if norm < 1e-8:
        return np.array([0.0, 0.0, 1.0], dtype=float)
    return arr / norm


def _plotly_camera_from_scene(scene: dict, style: dict) -> dict:
    eye_distance = float(style.get("camera_eye_distance", 1.8))
    eye = _normalize(scene.get("view_direction", [0.0, 0.0, 1.0]), [0.0, 0.0, 1.0]) * eye_distance
    up = _normalize(scene.get("up", [0.0, 1.0, 0.0]), [0.0, 1.0, 0.0])
    return {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
        "projection": {"type": str(style.get("projection", "perspective"))},
    }


def _visible_atoms(scene: dict, style: dict):
    atoms = scene["draw_atoms"]
    if style.get("show_minor_only", False):
        atoms = [atom for atom in atoms if atom["is_minor"]]
    return atoms or scene["draw_atoms"]


def _axis_triad_segments(scene: dict, style: dict):
    if "bounds" not in scene:
        return [], []
    mins = np.array(scene["bounds"]["mins"], dtype=float)
    screen_span = max(scene["bounds"]["screen_ranges"])
    offset = 0.10 * screen_span
    origin = mins - offset * np.array(scene["view_x"], dtype=float)
    origin -= offset * np.array(scene["view_y"], dtype=float)
    scale = float(style.get("axis_scale", 0.14)) * screen_span

    labels = style.get("axes_labels") or ["a", "b", "c"]
    labels = list(labels) + ["", "", ""]  # pad defensively
    segments: list[tuple[np.ndarray, np.ndarray]] = []
    label_positions: list[tuple[np.ndarray, str]] = []
    for vec, label in zip(
        [scene["M"][0], scene["M"][1], scene["M"][2]],
        labels[:3],
    ):
        v = _normalize(vec, [1.0, 0.0, 0.0])
        end = origin + v * scale
        segments.append((origin.copy(), end))
        label_positions.append((end, label))
    return segments, label_positions


def _scene_ranges(scene: dict, style: dict, topology_data: dict | None = None):
    """Compute ``[xr, yr, zr]`` axis ranges for the Plotly scene.

    A scene-level ``viewport`` override (set by :func:`uniform_viewport`) wins
    unconditionally; this is how caller code pins several scenes to a shared
    world cube so they render at identical screen scale.

    Otherwise the bounds are inflated by each atom's **visual radius** (rather
    than a blanket 18 % fractional pad) so spheres — especially large halides
    like Cl, Br, I — are never clipped at the panel edge. Unit-cell corners and
    topology markers expand the box but do not contribute radii.
    """
    override = scene.get("viewport")
    if override:
        return [
            [float(override["x"][0]), float(override["x"][1])],
            [float(override["y"][0]), float(override["y"][1])],
            [float(override["z"][0]), float(override["z"][1])],
        ]

    atoms = _visible_atoms(scene, style)
    atom_scale = float(style.get("atom_scale", 1.0))

    atom_mins = None
    atom_maxs = None
    if atoms:
        carts = np.array([atom["cart"] for atom in atoms], dtype=float)
        radii = np.array(
            [max(float(atom.get("atom_radius", 0.18)), 0.05) for atom in atoms],
            dtype=float,
        ) * atom_scale
        atom_mins = (carts - radii[:, None]).min(axis=0)
        atom_maxs = (carts + radii[:, None]).max(axis=0)

    extras = []
    if style.get("show_unit_cell", False):
        a = np.array(scene["M"][0], dtype=float)
        b = np.array(scene["M"][1], dtype=float)
        c = np.array(scene["M"][2], dtype=float)
        for corner in (
            np.zeros(3, dtype=float),
            a, b, c, a + b, a + c, b + c, a + b + c,
        ):
            extras.append(corner)
    if topology_data:
        center = topology_data.get("center_coords")
        if center is not None:
            extras.append(np.array(center, dtype=float))
        for point in topology_data.get("shell_coords") or []:
            extras.append(np.array(point, dtype=float))
        for overlay in topology_data.get("extra_overlays") or []:
            ovc = overlay.get("center_coords")
            if ovc is not None:
                extras.append(np.array(ovc, dtype=float))
            for point in overlay.get("shell_coords") or []:
                extras.append(np.array(point, dtype=float))
    if extras:
        extras_arr = np.array(extras, dtype=float)
        extras_min = extras_arr.min(axis=0)
        extras_max = extras_arr.max(axis=0)
        if atom_mins is None:
            atom_mins, atom_maxs = extras_min, extras_max
        else:
            atom_mins = np.minimum(atom_mins, extras_min)
            atom_maxs = np.maximum(atom_maxs, extras_max)

    if atom_mins is None:
        return [[-1.0, 1.0], [-1.0, 1.0], [-1.0, 1.0]]

    span = np.maximum(atom_maxs - atom_mins, 0.8)
    # Small breathing-room pad layered on top of radius-aware bounds.
    pad = np.maximum(span * 0.06, 0.25)
    mins = atom_mins - pad
    maxs = atom_maxs + pad
    return [
        [float(mins[0]), float(maxs[0])],
        [float(mins[1]), float(maxs[1])],
        [float(mins[2]), float(maxs[2])],
    ]


def uniform_viewport(scenes, *, style=None, padding=0.0):
    """Stamp a shared world-cube viewport on each scene so ``build_figure``
    renders them at identical screen scale.

    For every scene the viewport becomes a cube centred on that scene's own
    atom-bounding centroid. The cube side length equals the largest
    radius-aware axis-aligned span across **all** input scenes (+ ``padding``
    in Å on every side). Callers that later draw the scenes in a grid get
    panels with a single physical length scale — no more "small molecule
    ballooning to fill the panel while the big one shrinks to pinheads".

    The ``viewport`` key is written in-place on each scene dict. Subsequent
    calls to :func:`_scene_ranges` (and therefore :func:`build_figure`) honour
    it and skip their own bounds calculation.

    Parameters
    ----------
    scenes
        Iterable of scene dicts (as returned by ``build_scene_from_cif`` /
        ``build_scene_from_atoms``).
    style
        Optional style dict used to infer ``atom_scale``. When omitted, each
        scene's own ``scene["style"]`` is consulted with a default of 1.0.
    padding
        Extra padding in Å added symmetrically to every face of the cube.

    Returns
    -------
    list[dict]
        The stamped ``viewport`` dicts, one per scene, in the order the
        scenes were provided.
    """
    scenes = list(scenes)
    if not scenes:
        return []

    radius_spans = []
    centroids = []
    for scene in scenes:
        scn_style = style if style is not None else scene.get("style") or {}
        atom_scale = float(scn_style.get("atom_scale", 1.0))
        atoms = scene.get("draw_atoms") or []
        if not atoms:
            radius_spans.append(1.0)
            centroids.append(np.zeros(3, dtype=float))
            continue
        carts = np.array([atom["cart"] for atom in atoms], dtype=float)
        radii = np.array(
            [max(float(atom.get("atom_radius", 0.18)), 0.05) for atom in atoms],
            dtype=float,
        ) * atom_scale
        mins = (carts - radii[:, None]).min(axis=0)
        maxs = (carts + radii[:, None]).max(axis=0)
        radius_spans.append(float((maxs - mins).max()))
        centroids.append(0.5 * (mins + maxs))

    half = 0.5 * max(radius_spans) + float(padding)
    viewports = []
    for scene, center in zip(scenes, centroids):
        viewport = {
            "x": [float(center[0] - half), float(center[0] + half)],
            "y": [float(center[1] - half), float(center[1] + half)],
            "z": [float(center[2] - half), float(center[2] + half)],
            "center": [float(center[0]), float(center[1]), float(center[2])],
            "half_span": float(half),
        }
        scene["viewport"] = viewport
        viewports.append(viewport)
    return viewports


def _style_bool(style: dict, key: str, default: bool = False) -> bool:
    return bool(style.get(key, default))


def style_from_controls(
    atom_scale,
    bond_radius,
    minor_opacity,
    axis_scale,
    options,
    *,
    material: str | None = None,
    render_style: str | None = None,
    disorder: str | None = None,
    ortep_mode: str | None = None,
) -> dict:
    options = set(options or [])
    resolved_material = material or ("flat" if "fast_rendering" in options else "mesh")
    resolved_disorder = disorder or ("outline_rings" if "minor_wireframe" in options else "opacity")
    style = {
        "atom_scale": float(atom_scale),
        "bond_radius": float(bond_radius),
        "material": resolved_material,
        "style": render_style or "ball_stick",
        "disorder": resolved_disorder,
        "minor_opacity": float(minor_opacity),
        "axis_scale": float(axis_scale),
        "show_labels": "labels" in options,
        "show_axes": "axes" in options,
        "show_minor_only": "minor_only" in options,
        "minor_wireframe": "minor_wireframe" in options,
        "show_hydrogen": "hydrogens" in options,
        "show_unit_cell": "unit_cell_box" in options,
        "fast_rendering": "fast_rendering" in options,
        "topology_enabled": "topology" in options,
        "monochrome": "monochrome" in options,
    }
    if ortep_mode is not None:
        style["ortep_mode"] = ortep_mode
    return validate_style_schema(style)


def _unit_sphere(lat_steps: int = 9, lon_steps: int = 14) -> Tuple[np.ndarray, np.ndarray]:
    vertices = []
    for lat_idx in range(lat_steps + 1):
        theta = math.pi * lat_idx / lat_steps
        for lon_idx in range(lon_steps):
            phi = 2.0 * math.pi * lon_idx / lon_steps
            vertices.append(
                [
                    math.sin(theta) * math.cos(phi),
                    math.sin(theta) * math.sin(phi),
                    math.cos(theta),
                ]
            )
    triangles = []
    for lat_idx in range(lat_steps):
        for lon_idx in range(lon_steps):
            next_lon = (lon_idx + 1) % lon_steps
            a = lat_idx * lon_steps + lon_idx
            b = lat_idx * lon_steps + next_lon
            c = (lat_idx + 1) * lon_steps + lon_idx
            d = (lat_idx + 1) * lon_steps + next_lon
            triangles.append([a, c, b])
            triangles.append([b, c, d])
    return np.array(vertices, dtype=float), np.array(triangles, dtype=int)


def _append_mesh(mesh: dict, vertices: np.ndarray, triangles: np.ndarray):
    base = len(mesh["x"])
    mesh["x"].extend(vertices[:, 0].tolist())
    mesh["y"].extend(vertices[:, 1].tolist())
    mesh["z"].extend(vertices[:, 2].tolist())
    mesh["i"].extend((triangles[:, 0] + base).tolist())
    mesh["j"].extend((triangles[:, 1] + base).tolist())
    mesh["k"].extend((triangles[:, 2] + base).tolist())


def _sphere_mesh(center: Iterable[float], radius: float, lat_steps: int = 9, lon_steps: int = 14):
    unit_vertices, unit_triangles = _unit_sphere(lat_steps=lat_steps, lon_steps=lon_steps)
    center = np.array(center, dtype=float)
    vertices = unit_vertices * float(radius) + center[None, :]
    return vertices, unit_triangles


def _sphere_mesh_batch(centers: Iterable[Iterable[float]], radii: Iterable[float], lat_steps: int = 9, lon_steps: int = 14):
    centers_arr = np.asarray(list(centers), dtype=float).reshape(-1, 3)
    radii_arr = np.asarray(list(radii), dtype=float).reshape(-1)
    if len(centers_arr) == 0:
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    unit_vertices, unit_triangles = _unit_sphere(lat_steps=lat_steps, lon_steps=lon_steps)
    vertices = unit_vertices[None, :, :] * radii_arr[:, None, None] + centers_arr[:, None, :]
    n_unit_vertices = len(unit_vertices)
    triangles = unit_triangles[None, :, :] + (np.arange(len(centers_arr)) * n_unit_vertices)[:, None, None]
    return vertices.reshape(-1, 3), triangles.reshape(-1, 3)


def _cylinder_mesh(p0: Iterable[float], p1: Iterable[float], radius: float, sides: int = 8):
    start = np.array(p0, dtype=float)
    end = np.array(p1, dtype=float)
    axis = end - start
    length = np.linalg.norm(axis)
    if length < 1e-8:
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    axis /= length
    ref = np.array([0.0, 0.0, 1.0], dtype=float)
    if abs(np.dot(axis, ref)) > 0.92:
        ref = np.array([0.0, 1.0, 0.0], dtype=float)
    u = np.cross(axis, ref)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)

    ring0 = []
    ring1 = []
    for idx in range(sides):
        ang = 2.0 * math.pi * idx / sides
        offset = math.cos(ang) * u * radius + math.sin(ang) * v * radius
        ring0.append(start + offset)
        ring1.append(end + offset)
    vertices = np.array(ring0 + ring1 + [start, end], dtype=float)
    cap0 = len(vertices) - 2
    cap1 = len(vertices) - 1
    triangles = []
    for idx in range(sides):
        nxt = (idx + 1) % sides
        a0 = idx
        a1 = nxt
        b0 = idx + sides
        b1 = nxt + sides
        triangles.extend([[a0, b0, a1], [a1, b0, b1], [cap0, a1, a0], [cap1, b0, b1]])
    return vertices, np.array(triangles, dtype=int)


def _cylinder_mesh_batch(segments, radius: float, sides: int = 8):
    segments = list(segments)
    if not segments:
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    starts = np.asarray([seg[0] for seg in segments], dtype=float)
    ends = np.asarray([seg[1] for seg in segments], dtype=float)
    axes = ends - starts
    lengths = np.linalg.norm(axes, axis=1)
    valid = lengths >= 1e-8
    if not np.any(valid):
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    starts = starts[valid]
    ends = ends[valid]
    axes = axes[valid] / lengths[valid, None]

    refs = np.tile(np.array([0.0, 0.0, 1.0], dtype=float), (len(axes), 1))
    refs[np.abs(axes @ np.array([0.0, 0.0, 1.0], dtype=float)) > 0.92] = np.array([0.0, 1.0, 0.0])
    u = np.cross(axes, refs)
    u /= np.linalg.norm(u, axis=1)[:, None]
    v = np.cross(axes, u)

    angles = np.linspace(0.0, 2.0 * math.pi, int(sides), endpoint=False)
    offsets = (
        np.cos(angles)[None, :, None] * u[:, None, :]
        + np.sin(angles)[None, :, None] * v[:, None, :]
    ) * float(radius)
    ring0 = starts[:, None, :] + offsets
    ring1 = ends[:, None, :] + offsets
    vertices = np.concatenate([ring0, ring1, starts[:, None, :], ends[:, None, :]], axis=1)

    local_tris = []
    cap0 = 2 * int(sides)
    cap1 = cap0 + 1
    for idx in range(int(sides)):
        nxt = (idx + 1) % int(sides)
        a0 = idx
        a1 = nxt
        b0 = idx + int(sides)
        b1 = nxt + int(sides)
        local_tris.extend([[a0, b0, a1], [a1, b0, b1], [cap0, a1, a0], [cap1, b0, b1]])
    local_tris_arr = np.asarray(local_tris, dtype=int)
    n_vertices_per_segment = 2 * int(sides) + 2
    triangles = local_tris_arr[None, :, :] + (np.arange(len(starts)) * n_vertices_per_segment)[:, None, None]
    return vertices.reshape(-1, 3), triangles.reshape(-1, 3)


def _atom_selection_trace(scene: dict, style: dict, hidden_labels: set | None = None):
    xs, ys, zs, sizes, labels, customdata = [], [], [], [], [], []
    hidden_labels = hidden_labels or set()
    fragment_labels = scene.get("atom_fragment_labels") or []
    for idx, atom in enumerate(scene["draw_atoms"]):
        # Phase 2: don't expose a hover/click target for an atom that's
        # not visually present (atom_groups visible:false). Otherwise
        # a click on the empty cell still selects an invisible atom.
        if str(atom.get("label")) in hidden_labels:
            continue
        if not _atom_render_visible(atom):
            continue
        xs.append(float(atom["cart"][0]))
        ys.append(float(atom["cart"][1]))
        zs.append(float(atom["cart"][2]))
        sizes.append(max(6.0, 48.0 * atom["atom_radius"] * float(style["atom_scale"])))
        labels.append(atom["label"])
        # Phase 4: customdata schema is now ``[kind, idx, label, elem,
        # is_minor, fragment_label]`` so the right-click handler can
        # demux on ``kind`` (atom / bond / polyhedron) without having
        # to track which trace name was hit. Pre-Phase 4 callers
        # (existing click handlers) read ``customdata[1]`` for the
        # atom index; the kind tag at index 0 is additive so they
        # keep working against the same trace.
        frag_label = (
            str(fragment_labels[idx]) if idx < len(fragment_labels) and fragment_labels[idx] is not None else ""
        )
        customdata.append([
            "atom",
            int(idx),
            str(atom["label"]),
            str(atom["elem"]),
            int(atom["is_minor"]),
            frag_label,
        ])
    return _annotate_trace(go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker=dict(size=sizes, color="rgba(0,0,0,0)", opacity=0.02),
        customdata=customdata,
        hovertemplate="%{customdata[2]} (%{customdata[3]})<extra></extra>",
        showlegend=False,
        name="atom-selection",
    ), "atom_selection")


def _polyhedron_selection_trace(topology_data: dict | None) -> "go.Scatter3d | None":
    """Invisible Scatter3d markers at every polyhedron centre, carrying
    ``customdata=[kind, spec_id, fragment_label, is_anchor]`` so the
    right-click menu can identify which polyhedron the user picked.

    Plotly's Mesh3d accepts neither per-face nor per-vertex customdata,
    so a separate Scatter3d marker layer is the only way to make
    polyhedron centres click-targetable. The markers are placed at the
    centroid of each overlay (matching ``_world_sphere_marker_trace``'s
    geometry) and rendered fully transparent; the rounded marker size
    and ``hoverinfo="text"`` keep the hover hit area generous enough
    that the user doesn't have to land exactly on a face vertex.
    """
    if not topology_data:
        return None
    spec_results = topology_data.get("spec_results") or []
    if not spec_results:
        return None
    xs, ys, zs, customdata, hover = [], [], [], [], []
    for entry in spec_results:
        spec_id = str(entry.get("spec_id") or "")
        spec_name = str(entry.get("name") or "")
        for overlay in entry.get("overlays") or []:
            center = overlay.get("center_coords")
            if center is None or not overlay.get("visible", True):
                continue
            label = str(overlay.get("center_label") or "")
            xs.append(float(center[0]))
            ys.append(float(center[1]))
            zs.append(float(center[2]))
            customdata.append([
                "polyhedron",
                spec_id,
                label,
                int(bool(overlay.get("is_analysis_anchor"))),
            ])
            hover.append(f"{spec_name} \u00b7 {label}")
    if not xs:
        return None
    return go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker=dict(size=18, color="rgba(0,0,0,0)", opacity=0.02),
        customdata=customdata,
        hovertext=hover,
        hovertemplate="%{hovertext}<extra></extra>",
        showlegend=False,
        name="polyhedron-selection",
    )


def _bond_selection_trace(scene: dict, style: dict) -> "go.Scatter3d | None":
    """Invisible Scatter3d markers at each bond midpoint, carrying
    ``customdata=[kind, label_pair, elem_pair, is_minor]`` so the
    right-click menu has somewhere to land for chemical-bond picks.

    Like :func:`_polyhedron_selection_trace` this is a separate marker
    layer; the bond-mesh path (cylinders) cannot expose hover targets.
    Half-bond endpoints with ``_render_visible=False`` are skipped so
    a hidden bond doesn't generate a phantom hover target.
    """
    bonds = scene.get("bonds") or []
    atoms = scene.get("draw_atoms") or []
    if not bonds:
        return None
    n = len(atoms)
    xs, ys, zs, customdata, hover = [], [], [], [], []
    for bond in bonds:
        if not bool(bond.get("_render_visible", True)):
            continue
        i = int(bond.get("i", -1))
        j = int(bond.get("j", -1))
        if not (0 <= i < n and 0 <= j < n):
            continue
        if not _atom_render_visible(atoms[i]) or not _atom_render_visible(atoms[j]):
            continue
        start = np.asarray(bond["start"], dtype=float)
        end = np.asarray(bond["end"], dtype=float)
        mid = (start + end) / 2.0
        label_i = str(atoms[i].get("label") or "")
        label_j = str(atoms[j].get("label") or "")
        elem_i = str(atoms[i].get("elem") or "")
        elem_j = str(atoms[j].get("elem") or "")
        xs.append(float(mid[0]))
        ys.append(float(mid[1]))
        zs.append(float(mid[2]))
        customdata.append([
            "bond",
            f"{label_i}-{label_j}",
            f"{elem_i}-{elem_j}",
            int(bool(bond.get("is_minor"))),
        ])
        hover.append(f"{label_i} \u2014 {label_j}")
    if not xs:
        return None
    return _annotate_trace(go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker=dict(size=10, color="rgba(0,0,0,0)", opacity=0.02),
        customdata=customdata,
        hovertext=hover,
        hovertemplate="%{hovertext}<extra></extra>",
        showlegend=False,
        name="bond-selection",
    ), "bond_selection")


def _bond_segments(scene: dict, style: dict, *, with_scales: bool = False):
    """Yield ``(color, is_minor, start, end)`` tuples for every bond half.

    When ``with_scales=True`` each yield is extended with
    ``(radius_scale, opacity_scale)`` (floats, default 1.0) so callers
    that build mesh traces can bucket on the bond_groups radius/opacity
    overrides. Default ``False`` keeps the legacy 4-tuple API for the
    other callers (cylinder schematic / line traces) that don't need
    per-bond cosmetics.

    A ``style["force_bond_color"]`` (hex string) overrides per-atom bond
    colouring without touching any other colour in the scene.  This is the
    knob the open-ellipsoid ORTEP path uses to render every bond as plain
    black ink, matching the publication ORTEP-III convention without
    forcing ``monochrome=True`` (which would also blacken atom fills).
    """
    forced = style.get("force_bond_color")
    atoms = scene.get("draw_atoms") or []
    n_atoms = len(atoms)
    for bond in scene["bonds"]:
        if style.get("show_minor_only", False) and not bond["is_minor"]:
            continue
        # Phase 4: bond_groups can mark a bond invisible directly. We
        # honour both the bond-level ``_render_visible`` (set by
        # ``tag_bonds_with_groups``) and the per-atom visibility (set
        # by ``tag_atoms_with_groups``); a half-bond that survives
        # both is drawn.
        if not bool(bond.get("_render_visible", True)):
            continue
        i = int(bond.get("i", -1))
        j = int(bond.get("j", -1))
        if 0 <= i < n_atoms and not _atom_render_visible(atoms[i]):
            continue
        if 0 <= j < n_atoms and not _atom_render_visible(atoms[j]):
            continue
        start = np.array(bond["start"], dtype=float)
        end = np.array(bond["end"], dtype=float)
        mid = (start + end) / 2.0
        # Per-bond ``_render_color`` (bond_groups override) wins over
        # everything except ``style.force_bond_color`` (which is the
        # global "publication ORTEP-III black ink" knob).
        bond_render_color = bond.get("_render_color")
        if bond_render_color:
            i_color = forced if forced else bond_render_color
            j_color = forced if forced else bond_render_color
        else:
            i_color = forced if forced else (atoms[i].get("_render_color") if 0 <= i < n_atoms else None) or _style_color(bond["color_i"], style)
            j_color = forced if forced else (atoms[j].get("_render_color") if 0 <= j < n_atoms else None) or _style_color(bond["color_j"], style)
        c_i = i_color
        c_j = j_color
        radius_scale = float(bond.get("_render_radius_scale", 1.0) or 1.0)
        opacity_scale = float(bond.get("_render_opacity_scale", 1.0) or 1.0)
        opacity_group = _bond_opacity_group_id(bond)
        halves = [
            (c_i, bond["is_minor"], start, mid),
            (c_j, bond["is_minor"], mid, end),
        ]
        for color, is_minor, seg_start, seg_end in halves:
            if is_minor and style.get("disorder") == "dashed_bonds":
                length = float(np.linalg.norm(seg_end - seg_start))
                dash_len = max(0.08, 0.22 * length)
                gap_len = max(0.05, 0.14 * length)
                for dash_start, dash_end in _dashed_segments([(seg_start, seg_end)], dash_len=dash_len, gap_len=gap_len):
                    if with_scales:
                        yield color, is_minor, dash_start, dash_end, radius_scale, opacity_scale, opacity_group
                    else:
                        yield color, is_minor, dash_start, dash_end
            else:
                if with_scales:
                    yield color, is_minor, seg_start, seg_end, radius_scale, opacity_scale, opacity_group
                else:
                    yield color, is_minor, seg_start, seg_end


def _bond_mesh_traces(scene: dict, style: dict):
    """Build the bond Mesh3d traces, bucketed by ``(color, is_minor,
    radius_bin, opacity_bin)`` so per-bond ``_render_radius_scale`` /
    ``_render_opacity_scale`` (set by ``tag_bonds_with_groups``)
    survive the one-trace-per-colour grouping. Plotly bakes opacity
    onto the trace, not per-vertex; the same is true of ``color``;
    so we have to expand the bucket key to keep their distinct
    cosmetic values from collapsing."""
    groups: Dict[Tuple[str, bool, int, str | None], dict] = {}
    base_radius = max(0.04, float(style["bond_radius"]))
    for color, is_minor, start, end, radius_scale, opacity_scale, opacity_group in _bond_segments(
        scene, style, with_scales=True
    ):
        # Bin to two decimals so e.g. a 1.50 vs 1.51 slider tick doesn't
        # fragment the trace list. Same trick is used in _atom_mesh_traces.
        radius_bin = int(round(float(radius_scale) * 100))
        key = (color, is_minor, radius_bin, opacity_group)
        groups.setdefault(
            key,
            {"segments": [], "radius_scale": radius_scale, "opacity_scale": opacity_scale, "opacity_group": opacity_group},
        )["segments"].append((start, end))

    traces = []
    for (color, is_minor, _r_bin, opacity_group), payload in groups.items():
        radius_scale = float(payload["radius_scale"])
        opacity_scale = float(payload["opacity_scale"])
        radius = base_radius * radius_scale * (
            float(style.get("minor_bond_scale", 0.82)) if is_minor else 1.0
        )
        vertices, triangles = _cylinder_mesh_batch(
            payload["segments"],
            radius,
            sides=6,
        )
        if len(vertices) == 0:
            continue
        traces.append(
            _annotate_trace(go.Mesh3d(
                x=vertices[:, 0],
                y=vertices[:, 1],
                z=vertices[:, 2],
                i=triangles[:, 0],
                j=triangles[:, 1],
                k=triangles[:, 2],
                color=color,
                opacity=_minor_opacity_for(style, is_minor) * opacity_scale,
                hoverinfo="skip",
                showlegend=False,
                flatshading=False,
            ), "bond", is_minor=is_minor, opacity_group=opacity_group)
        )
    return traces


def _atom_mesh_traces(scene: dict, style: dict):
    # Per-atom tessellation budget. The over-the-wire cost of one
    # sphere is ``(lat-1)*lon + 2`` Mesh3d verts × (3 × 4 B for
    # f32 coords + faces). For a 200-atom DAP-4 unit cell with
    # topology overlay the figure JSON used to be ~1.4 MB; dropping
    # subdivision halves the vertex count and gets the brotli-
    # compressed wire size into the ~120 kB range, where a Labels
    # toggle round-trips in well under a second on most consumer
    # connections. The visual difference vs the old 6/10 default
    # is invisible at the camera distance forced by a dense unit
    # cell. Users who insist on perfectly smooth balls pick the
    # "formula unit" Display Scope (n_atoms < 60).
    n_atoms = len(scene.get("draw_atoms", []))
    if n_atoms > 400:
        lat_steps, lon_steps = 3, 6
    elif n_atoms > 150:
        lat_steps, lon_steps = 4, 7
    elif n_atoms > 60:
        lat_steps, lon_steps = 5, 9
    else:
        lat_steps, lon_steps = 6, 10
    # Bucket key extends to (color, is_minor, opacity_scale_bin) so
    # per-group ``opacity`` overrides survive the Mesh3d
    # one-trace-per-colour grouping (Plotly bakes opacity into the
    # trace, not per-vertex). Quantise the scale to two decimals so a
    # slider that emits 0.523 vs 0.524 doesn't fragment the trace
    # list and tank the figure-JSON cache hit rate.
    # Bucket key extends to (color, is_minor, effective_opacity_bin) so
    # per-group ``opacity`` overrides survive the Mesh3d
    # one-trace-per-colour grouping (Plotly bakes opacity into the
    # trace, not per-vertex). Quantise the opacity to two decimals so a
    # slider that emits 0.523 vs 0.524 doesn't fragment the trace
    # list and tank the figure-JSON cache hit rate.
    groups: Dict[Tuple[str, bool, str | None], dict] = {}
    for atom in scene["draw_atoms"]:
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        if not _atom_render_visible(atom):
            continue
        color = _atom_render_color(atom, style, light=atom["is_minor"])
        eff_opacity = _atom_effective_opacity(atom, style)
        opacity_group = _atom_opacity_group_id(atom)
        key = (color, atom["is_minor"], opacity_group)
        groups.setdefault(key, {"centers": [], "radii": [], "opacity": eff_opacity, "opacity_group": opacity_group})
        radius = float(atom["atom_radius"]) * float(style["atom_scale"])
        if atom["is_minor"]:
            radius *= 1.12
        groups[key]["centers"].append(atom["cart"])
        groups[key]["radii"].append(radius)

    traces = []
    for (color, is_minor, opacity_group), payload in groups.items():
        vertices, triangles = _sphere_mesh_batch(
            payload["centers"],
            payload["radii"],
            lat_steps=lat_steps,
            lon_steps=lon_steps,
        )
        traces.append(
            _annotate_trace(go.Mesh3d(
                x=vertices[:, 0],
                y=vertices[:, 1],
                z=vertices[:, 2],
                i=triangles[:, 0],
                j=triangles[:, 1],
                k=triangles[:, 2],
                color=color,
                opacity=payload["opacity"],
                hoverinfo="skip",
                showlegend=False,
                flatshading=False,
            ), "atom", is_minor=is_minor, opacity_group=opacity_group)
        )
    return traces


def _bond_scatter_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool], list[list[float]]] = {}
    for color, is_minor, start, end in _bond_segments(scene, style):
        groups.setdefault((color, is_minor), []).append([start, end])

    traces = []
    base_width = max(4.0, 24.0 * float(style["bond_radius"]))
    for (color, is_minor), segments in groups.items():
        xs, ys, zs = [], [], []
        for start, end in segments:
            xs.extend([float(start[0]), float(end[0]), None])
            ys.extend([float(start[1]), float(end[1]), None])
            zs.extend([float(start[2]), float(end[2]), None])
        traces.append(
            _annotate_trace(go.Scatter3d(
                x=xs,
                y=ys,
                z=zs,
                mode="lines",
                line=dict(
                    color=color,
                    width=base_width * (float(style.get("minor_bond_scale", 0.82)) if is_minor else 1.0),
                    dash="dash" if is_minor and style.get("disorder") == "dashed_bonds" else "solid",
                ),
                opacity=_minor_opacity_for(style, is_minor),
                hoverinfo="skip",
                showlegend=False,
            ), "bond", is_minor=is_minor)
        )
    return traces


def _atom_scatter_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool, str, str | None], dict] = {}
    fragment_labels = scene.get("atom_fragment_labels") or []
    for idx, atom in enumerate(scene["draw_atoms"]):
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        if not _atom_render_visible(atom):
            continue
        color = _atom_render_color(atom, style, light=atom["is_minor"])
        eff_opacity = _atom_effective_opacity(atom, style)
        opacity_group = _atom_opacity_group_id(atom)
        # Per-trace key = (element, is_minor, effective_color, effective_opacity_bin).
        # Adding colour to the key means a per-element atom_groups
        # rule still groups its atoms in one Scatter3d (so legend
        # entries still read element-by-element) but doesn't merge
        # red-O with default-O when the user splits them.
        key = (atom["elem"], atom["is_minor"], color, opacity_group)
        groups.setdefault(
            key,
            {"x": [], "y": [], "z": [], "size": [], "text": [], "color": color, "customdata": [], "opacity": eff_opacity},
        )
        base_size = max(10.0, 95.0 * atom["atom_radius"] * float(style["atom_scale"]))
        groups[key]["x"].append(float(atom["cart"][0]))
        groups[key]["y"].append(float(atom["cart"][1]))
        groups[key]["z"].append(float(atom["cart"][2]))
        groups[key]["size"].append(base_size * (1.12 if atom["is_minor"] else 1.0))
        groups[key]["text"].append(atom["label"])
        frag_label = (
            str(fragment_labels[idx]) if idx < len(fragment_labels) and fragment_labels[idx] is not None else ""
        )
        groups[key]["customdata"].append([
            "atom",
            int(idx),
            str(atom["label"]),
            str(atom["elem"]),
            int(atom["is_minor"]),
            frag_label,
        ])

    traces = []
    for (elem, is_minor, _color, opacity_group), payload in groups.items():
        traces.append(
            _annotate_trace(go.Scatter3d(
                x=payload["x"],
                y=payload["y"],
                z=payload["z"],
                mode="markers",
                text=payload["text"],
                customdata=payload["customdata"],
                hovertemplate="%{text}<extra></extra>",
                marker=dict(
                    size=payload["size"],
                    color=payload["color"],
                    opacity=payload["opacity"],
                    line=dict(color="#444444" if is_minor else payload["color"], width=3.5 if is_minor else 0),
                ),
                showlegend=False,
                name=f"{elem}{' minor' if is_minor else ''}",
            ), "atom", is_minor=is_minor, opacity_group=opacity_group)
        )
    return traces


def _minor_bond_wireframe_traces(scene: dict, style: dict):
    if style.get("disorder") not in ("outline_rings", "dashed_bonds") and not style.get("minor_wireframe", False):
        return []
    atoms = scene.get("draw_atoms") or []
    n_atoms = len(atoms)
    groups: Dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for bond in scene["bonds"]:
        if not bond["is_minor"]:
            continue
        # Phase 2: skip bonds whose endpoint atom was hidden by an
        # atom_groups ``visible: false`` rule -- otherwise the wireframe
        # ring sits in empty space and reads as a rendering bug.
        i = int(bond.get("i", -1))
        j = int(bond.get("j", -1))
        if 0 <= i < n_atoms and not _atom_render_visible(atoms[i]):
            continue
        if 0 <= j < n_atoms and not _atom_render_visible(atoms[j]):
            continue
        start = np.array(bond["start"], dtype=float)
        end = np.array(bond["end"], dtype=float)
        mid = (start + end) / 2.0
        i_color = (
            _atom_render_color(atoms[i], style, light=True)
            if 0 <= i < n_atoms
            else _style_color(bond.get("color_i", "#888888"), style)
        )
        j_color = (
            _atom_render_color(atoms[j], style, light=True)
            if 0 <= j < n_atoms
            else _style_color(bond.get("color_j", "#888888"), style)
        )
        groups.setdefault(i_color, []).append((start, mid))
        groups.setdefault(j_color, []).append((mid, end))
    if not groups:
        return []
    traces = []
    radius = max(0.015, 0.55 * float(style["bond_radius"]))
    for color, segments in groups.items():
        if style.get("disorder") == "dashed_bonds":
            lengths = [float(np.linalg.norm(end - start)) for start, end in segments]
            typical = float(np.median(lengths)) if lengths else 1.0
            segments = _dashed_segments(
                segments,
                dash_len=max(0.08, 0.18 * typical),
                gap_len=max(0.05, 0.12 * typical),
            )
        trace = _segment_cylinder_trace(
            segments,
            radius=radius,
            color=color,
            opacity=0.9,
            sides=4,
            name="minor-bond-wireframe",
        )
        if trace is not None:
            traces.append(_annotate_trace(trace, "bond", is_minor=True))
    return traces


def _wireframe_atom_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool], list[tuple[np.ndarray, np.ndarray]]] = {}
    axes = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ]
    for atom in scene["draw_atoms"]:
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        if not _atom_render_visible(atom):
            continue
        radius = max(0.05, float(atom["atom_radius"]) * float(style["atom_scale"]))
        key = (_atom_render_color(atom, style, light=atom["is_minor"]), atom["is_minor"])
        bucket = groups.setdefault(key, [])
        center = np.asarray(atom["cart"], dtype=float)
        for axis in axes:
            bucket.extend(_ring_segments(center, radius, axis, segments=18))

    traces = []
    for (color, is_minor), segments in groups.items():
        trace = _segment_cylinder_trace(
            segments,
            radius=max(0.008, 0.065 * float(style["bond_radius"])),
            color=color,
            opacity=_minor_opacity_for(style, is_minor),
            sides=4,
            name="wireframe-atoms",
        )
        if trace is not None:
            traces.append(_annotate_trace(trace, "atom", is_minor=is_minor))
    return traces


def _wireframe_bond_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool], list[tuple[np.ndarray, np.ndarray]]] = {}
    for color, is_minor, start, end in _bond_segments(scene, style):
        groups.setdefault((color, is_minor), []).append((start, end))
    traces = []
    for (color, is_minor), segments in groups.items():
        trace = _segment_cylinder_trace(
            segments,
            radius=max(0.01, 0.40 * float(style["bond_radius"])),
            color=color,
            opacity=_minor_opacity_for(style, is_minor),
            sides=4,
            name="wireframe-bonds",
        )
        if trace is not None:
            traces.append(_annotate_trace(trace, "bond", is_minor=is_minor))
    return traces


def _ring_segments(center: np.ndarray, radius: float, axis: np.ndarray, *, segments: int = 14):
    """Generate (start, end) line segments forming a circular ring of
    ``radius`` around ``center`` lying in the plane perpendicular to
    ``axis``. Used by the disorder-outline wireframe so the rings are
    real 3-D geometry that scales with the camera."""
    axis = np.asarray(axis, dtype=float)
    axis = axis / max(np.linalg.norm(axis), 1e-9)
    ref = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, ref)
    u = u / max(np.linalg.norm(u), 1e-9)
    v = np.cross(axis, u)
    pts = []
    for k in range(segments):
        theta = 2.0 * np.pi * k / segments
        pts.append(center + radius * (np.cos(theta) * u + np.sin(theta) * v))
    out = []
    for k in range(segments):
        out.append((pts[k], pts[(k + 1) % segments]))
    return out


def _minor_outline_traces(scene: dict, style: dict):
    """Wireframe sphere around each minor / disorder atom, built from
    three perpendicular rings of Mesh3d cylinders. Replaces the old
    ``Scatter3d(mode="markers", line=dict(width=...))`` rings whose
    pixel-fixed width meant the disorder outlines stayed the same
    screen size when the camera dollied out -- and ate the atoms whole
    once the structure shrank."""
    if style.get("disorder") not in ("outline_rings", "color_shift") and not style.get("minor_wireframe", False):
        return []
    groups: Dict[str, list[tuple[np.ndarray, float]]] = {}
    for atom in scene["draw_atoms"]:
        if not atom["is_minor"]:
            continue
        if not _atom_render_visible(atom):
            continue
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        ring_scale = 1.34 if style.get("minor_wireframe", False) else 1.20
        radius = float(atom["atom_radius"]) * float(style["atom_scale"]) * ring_scale
        color = _atom_render_color(atom, style, light=True)
        groups.setdefault(color, []).append((np.asarray(atom["cart"], dtype=float), radius))
    if not groups:
        return []
    cylinder_radius = 0.022 if style.get("minor_wireframe", False) else 0.014
    axes = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ]
    traces = []
    for color, minors in groups.items():
        segments: list[tuple[np.ndarray, np.ndarray]] = []
        for center, radius in minors:
            for axis in axes:
                segments.extend(_ring_segments(center, radius, axis, segments=14))
        trace = _segment_cylinder_trace(
            segments,
            radius=cylinder_radius,
            color=color,
            opacity=0.95,
            sides=4,
            name="minor-outline",
        )
        if trace is not None:
            traces.append(_annotate_trace(trace, "minor_overlay", is_minor=True))
    return traces


def _contact_traces(scene: dict, style: dict):
    """Render scene-level non-bonded contacts (e.g. ammonium...halide contacts).

    A "contact" is a thin dashed/dotted line segment specified in the same
    cartesian frame as the atoms, so it is guaranteed to land pixel-for-pixel
    on the rendered atom centres regardless of camera, viewport or panel
    aspect. Each contact dict supports::

        {"start": [x, y, z], "end": [x, y, z],
         "color": "#222222", "dash": "dot", "width": 4.0, "opacity": 0.9}

    Contacts whose endpoints fall outside the visible viewport are still
    drawn — Plotly only clips against the scene cube, never against the
    Matplotlib outer axes.
    """
    contacts = scene.get("contacts") or []
    if not contacts:
        return []
    groups: Dict[Tuple[str, str, float, float], list[list[float]]] = {}
    for c in contacts:
        try:
            start = [float(c["start"][i]) for i in range(3)]
            end = [float(c["end"][i]) for i in range(3)]
        except (KeyError, TypeError, IndexError, ValueError):
            continue
        color = str(c.get("color", "#1F1F1F"))
        dash = str(c.get("dash", "dot"))
        width = float(c.get("width", 4.0))
        opacity = float(c.get("opacity", 0.9))
        key = (color, dash, width, opacity)
        groups.setdefault(key, []).append([start, end])
    traces = []
    for (color, dash, width, opacity), segments in groups.items():
        xs, ys, zs = [], [], []
        for start, end in segments:
            xs.extend([start[0], end[0], None])
            ys.extend([start[1], end[1], None])
            zs.extend([start[2], end[2], None])
        traces.append(
            go.Scatter3d(
                x=xs,
                y=ys,
                z=zs,
                mode="lines",
                line=dict(color=color, width=width, dash=dash),
                opacity=opacity,
                hoverinfo="skip",
                showlegend=False,
                name="contact",
            )
        )
    return traces


def _highlight_traces(scene: dict, style: dict):
    if style.get("show_minor_only", False):
        return []
    light_dir = (
        -0.28 * np.array(scene["view_x"], dtype=float)
        + 0.34 * np.array(scene["view_y"], dtype=float)
        + 0.72 * np.array(scene["view_z"], dtype=float)
    )
    norm = np.linalg.norm(light_dir)
    if norm < 1e-8:
        return []
    light_dir /= norm

    groups: Dict[str, dict] = {}
    for atom in scene["draw_atoms"]:
        if atom["is_minor"] or atom["elem"] == "H":
            continue
        size = max(5.0, 55.0 * atom["atom_radius"] * float(style["atom_scale"]))
        center = np.array(atom["cart"], dtype=float) + light_dir * (atom["atom_radius"] * float(style["atom_scale"]) * 0.25)
        key = atom["color_light"]
        groups.setdefault(key, {"x": [], "y": [], "z": [], "size": []})
        groups[key]["x"].append(float(center[0]))
        groups[key]["y"].append(float(center[1]))
        groups[key]["z"].append(float(center[2]))
        groups[key]["size"].append(size)

    traces = []
    for color, payload in groups.items():
        traces.append(
            go.Scatter3d(
                x=payload["x"],
                y=payload["y"],
                z=payload["z"],
                mode="markers",
                marker=dict(
                    size=payload["size"],
                    color=color,
                    opacity=0.65,
                    line=dict(color="rgba(255,255,255,0.6)", width=1.5),
                ),
                hoverinfo="skip",
                showlegend=False,
            )
        )
    return traces


def _label_traces(scene: dict, style: dict, hidden_labels: set | None = None):
    hidden_labels = hidden_labels or set()
    cache = scene.setdefault("_label_trace_cache", {})
    # ``monochrome`` is inert once atom_groups is non-empty (see
    # ``_style_color``); collapse it in the key so the same cache hit
    # serves both pre- and post-migration callers.
    monochrome_effective = bool(style.get("monochrome", False)) and not style.get("atom_groups")
    key = (
        monochrome_effective,
        str(style.get("label_color", "#111111")),
        str(style.get("minor_label_color", "#666666")),
        round(float(style.get("label_font_size", 12)), 3),
        # Cache key carries the hidden set so toggling a "Hide H"
        # group rule actually re-emits the label trace; otherwise a
        # warm cache from before the rule still draws the H labels.
        tuple(sorted(hidden_labels)),
    )
    if key in cache:
        perf_log.record("cache:mesh", kind="cache", info={"hit": True, "entries": len(cache)})
        return cache[key]
    perf_log.record("cache:mesh", kind="cache", info={"hit": False, "entries": len(cache)})
    # Use a single font size for every atom label (was 10 vs 11 split by
    # minor-disorder flag, which read as inconsistent typography rather than
    # signalling "minor"). Disorder is conveyed by colour only; size stays uniform.
    label_size = float(style.get("label_font_size", 12))
    major_label_color = style.get("label_color", "#111111")
    minor_label_color = style.get("minor_label_color", "#666666")
    buckets = {
        False: {"x": [], "y": [], "z": [], "text": [], "color": major_label_color},
        True: {"x": [], "y": [], "z": [], "text": [], "color": minor_label_color},
    }
    for item in scene["label_items"]:
        if str(item.get("text")) in hidden_labels:
            continue
        bucket = buckets[item["is_minor"]]
        bucket["x"].append(float(item["label_cart"][0]))
        bucket["y"].append(float(item["label_cart"][1]))
        bucket["z"].append(float(item["label_cart"][2]))
        bucket["text"].append(item["text"])

    traces = []
    for is_minor, bucket in buckets.items():
        if not bucket["x"]:
            continue
        traces.append(
            _annotate_trace(go.Scatter3d(
                x=bucket["x"],
                y=bucket["y"],
                z=bucket["z"],
                mode="text",
                text=bucket["text"],
                textfont=dict(size=label_size, color=bucket["color"]),
                hoverinfo="skip",
                showlegend=False,
            ), "labels", is_minor=is_minor)
        )
    cache[key] = [_round_coord_arrays(tr.to_plotly_json()) for tr in traces]
    return cache[key]


def _axis_traces(scene: dict, style: dict):
    """Deprecated: the in-scene 3D axis triad has been retired.

    The 3D cylinder shafts foreshortened to invisible stubs on cameras
    aligned with a lattice vector, and the opposite case (oblique
    cameras on long cells like EMAP) drew a single long shaft through
    the structure. The ``show_axes`` toggle now drives the paper-coord
    compass via :func:`axis_key_overlay`, which always sits in a stable
    figure corner. Kept as an empty-list shim so any external caller
    that still imports the symbol keeps working.
    """
    return []


def axis_key_overlay(scene: dict, style: dict) -> tuple[list[dict], list[dict]]:
    """Build Plotly paper-coord annotations + shapes for a corner axis triad.

    The triad is rendered in **screen space** (paper coordinates) rather than
    inside the 3D scene, so labels and arrows live in a stable figure corner
    and cannot be clipped by the 3D viewport cube or a caller's outer
    matplotlib axes. Labels stack in a left-aligned vertical column (one per
    crystallographic axis, default order c → b → a top-to-bottom), with each
    label followed by a short arrow pointing in the *projected* direction of
    that axis. Arrow lengths are normalised so the longest projection fills
    ``axis_key_arrow_len`` while shorter axes preserve their relative length.

    The arrow body is drawn as a Plotly line ``shape`` and the arrowhead as a
    filled triangular path — both of which honour ``xref='paper'``. Labels
    are separate ``annotations`` objects. Returns ``(annotations, shapes)``.

    The in-app ``show_axes`` checkbox and the publication-style
    ``show_axis_key`` flag both feed this overlay; either being truthy
    renders the triad. Projections are read from
    ``scene["projected_axes"]`` (populated by :func:`scene.build_scene_from_atoms`)
    and the label strings come from ``style["axes_labels"]`` with stacking
    order controlled by ``style["axis_key_label_order"]``.
    """
    show_axes = bool(style.get("show_axes", False))
    show_axis_key = bool(style.get("show_axis_key", False))
    if not (show_axes or show_axis_key):
        return [], []
    projections = scene.get("projected_axes")
    if not projections or len(projections) < 3:
        return [], []

    axes_labels = list(style.get("axes_labels") or scene.get("axis_labels") or ["a", "b", "c"])[:3]
    label_to_proj = {axes_labels[i]: projections[i] for i in range(min(3, len(axes_labels)))}

    order = list(style.get("axis_key_label_order") or ["c", "b", "a"])
    order = [label for label in order if label in label_to_proj]
    if not order:
        return [], []

    anchor = style.get("axis_key_anchor") or [0.05, 0.07]
    anchor_x = float(anchor[0])
    anchor_y = float(anchor[1])
    row_gap = float(style.get("axis_key_row_gap", 0.095))
    arrow_len = float(style.get("axis_key_arrow_len", 0.085))
    if show_axes and not show_axis_key:
        # When the in-app "Axes" checkbox is the trigger, route the
        # matching "Axis Scale" slider (0.05-0.25) through this overlay so
        # the user-visible control keeps working. ``0.6`` keeps the
        # default slider value (0.14) near the publication preset
        # (``axis_key_arrow_len`` default 0.085).
        arrow_len = max(0.02, float(style.get("axis_scale", 0.14)) * 0.6)
    label_pad = float(style.get("axis_key_label_pad", 0.045))
    font_size = float(style.get("axis_key_font_size", 13))
    line_width = float(style.get("axis_key_line_width", 1.6))
    head_len = float(style.get("axis_key_head_len", 0.025))
    head_width = float(style.get("axis_key_head_width", 0.018))
    color = style.get("axis_key_color", "#2F2F2F")
    italic = bool(style.get("axis_key_italic", True))

    norms = [math.hypot(float(label_to_proj[label][0]), float(label_to_proj[label][1])) for label in order]
    max_norm = max(norms) if norms else 0.0
    if max_norm < 1e-8:
        return [], []

    # Cap arrow_len so the arrow's **vertical** extent (arrow_len * |dy/norm|)
    # can never exceed half the row gap. Without this clamp a steeply-
    # projecting axis on one row can shoot into the neighbouring row and
    # collide with that row's label, producing the "fragmented triad" look.
    # Share a single scale factor across all rows so relative lengths are
    # preserved.
    max_abs_uy = max(
        abs(float(label_to_proj[label][1]) / norm) if norm > 1e-8 else 0.0
        for label, norm in zip(order, norms)
    )
    if max_abs_uy > 1e-8:
        y_budget = 0.42 * row_gap
        arrow_len = min(arrow_len, y_budget / max_abs_uy)

    annotations: list[dict] = []
    shapes: list[dict] = []
    n_rows = len(order)
    for row_idx, label in enumerate(order):
        row_y = anchor_y + (n_rows - 1 - row_idx) * row_gap
        text = f"<i>{label}</i>" if italic else label
        annotations.append(dict(
            x=anchor_x, y=row_y,
            xref="paper", yref="paper",
            text=text,
            showarrow=False,
            xanchor="left", yanchor="middle",
            font=dict(size=font_size, color=color),
        ))
        dx, dy = label_to_proj[label]
        norm = math.hypot(float(dx), float(dy))
        if norm < 1e-8:
            continue
        ux = float(dx) / norm
        uy = float(dy) / norm
        # Scale arrow length by the axis's 2D projection magnitude so near-
        # perpendicular axes render as shorter arrows. Impose a minimum so
        # (a) near-perpendicular axes never collapse to an invisible speck
        # (the user would read that as a rendering bug) and (b) the shaft is
        # always longer than the arrowhead — otherwise the head's base
        # falls behind the arrow's own origin and the triad visibly
        # fragments into detached triangles.
        min_scale = 0.65
        rel = max(norm / max_norm, min_scale)
        length = max(arrow_len * rel, 1.35 * head_len)
        x0 = anchor_x + label_pad
        y0 = row_y
        x1 = x0 + length * ux
        y1 = y0 + length * uy
        # Arrow shaft (stops just short of the tip to avoid the arrowhead
        # line-width bleeding past the triangle on retina renders).
        shaft_end_x = x1 - 0.55 * head_len * ux
        shaft_end_y = y1 - 0.55 * head_len * uy
        shapes.append(dict(
            type="line",
            xref="paper", yref="paper",
            x0=x0, y0=y0,
            x1=shaft_end_x, y1=shaft_end_y,
            line=dict(color=color, width=line_width),
            layer="above",
        ))
        # Filled triangular arrowhead tip — points from (x1, y1) backward
        # along (-ux, -uy), with left/right base points straddling the
        # perpendicular (-uy, ux).
        base_cx = x1 - head_len * ux
        base_cy = y1 - head_len * uy
        px = -uy
        py = ux
        base_left_x = base_cx + 0.5 * head_width * px
        base_left_y = base_cy + 0.5 * head_width * py
        base_right_x = base_cx - 0.5 * head_width * px
        base_right_y = base_cy - 0.5 * head_width * py
        shapes.append(dict(
            type="path",
            xref="paper", yref="paper",
            path=(
                f"M {x1},{y1} "
                f"L {base_left_x},{base_left_y} "
                f"L {base_right_x},{base_right_y} Z"
            ),
            fillcolor=color,
            line=dict(color=color, width=0),
            layer="above",
        ))
    return annotations, shapes


def axis_key_annotations(scene: dict, style: dict) -> list[dict]:
    """Backwards-compatible wrapper returning only the annotations list.

    Prefer :func:`axis_key_overlay` which also returns paper-coord shapes for
    the arrow shafts and arrowheads.
    """
    annotations, _ = axis_key_overlay(scene, style)
    return annotations


def _segment_cylinder_trace(segments, *, radius: float, color: str, opacity: float = 0.95, sides: int = 5, name: str | None = None):
    """Materialise a list of (start, end) line segments as a single
    Mesh3d cylinder bundle. Unlike a Scatter3d ``line.width`` (pixels),
    the cylinder radius lives in world (Å) coordinates so the segment
    thickness scales with the camera zoom -- matching the rest of the
    scene geometry. ``sides=5`` keeps the per-edge triangle count low
    (10 verts per segment) so dense overlays stay cheap."""
    verts, tris = _cylinder_mesh_batch(segments, float(radius), sides=int(sides))
    if len(verts) == 0:
        return None
    return go.Mesh3d(
        x=verts[:, 0],
        y=verts[:, 1],
        z=verts[:, 2],
        i=tris[:, 0],
        j=tris[:, 1],
        k=tris[:, 2],
        color=color,
        opacity=opacity,
        flatshading=True,
        hoverinfo="skip",
        showlegend=False,
        name=name or "line-mesh",
    )


def _dashed_segments(segments, *, dash_len: float, gap_len: float):
    """Break each (start, end) into shorter sub-segments alternating
    drawn / skipped, so the rendered cylinder bundle reads as a dashed
    line. ``dash_len`` and ``gap_len`` are in world (Å) units."""
    out: list[tuple[np.ndarray, np.ndarray]] = []
    period = float(dash_len) + float(gap_len)
    if period <= 0:
        return list(segments)
    for start, end in segments:
        start = np.asarray(start, dtype=float)
        end = np.asarray(end, dtype=float)
        vec = end - start
        length = float(np.linalg.norm(vec))
        if length < 1e-8:
            continue
        direction = vec / length
        cursor = 0.0
        while cursor < length:
            seg_start = start + direction * cursor
            seg_end_dist = min(length, cursor + float(dash_len))
            seg_end = start + direction * seg_end_dist
            out.append((seg_start, seg_end))
            cursor += period
    return out


def _unit_cell_traces(scene: dict, style: dict):
    origin = np.zeros(3, dtype=float)
    a = np.array(scene["M"][0], dtype=float)
    b = np.array(scene["M"][1], dtype=float)
    c = np.array(scene["M"][2], dtype=float)
    corners = {
        "000": origin,
        "100": a,
        "010": b,
        "001": c,
        "110": a + b,
        "101": a + c,
        "011": b + c,
        "111": a + b + c,
    }
    edges = [
        ("000", "100"), ("000", "010"), ("000", "001"),
        ("100", "110"), ("100", "101"),
        ("010", "110"), ("010", "011"),
        ("001", "101"), ("001", "011"),
        ("110", "111"), ("101", "111"), ("011", "111"),
    ]
    segments = [(corners[s], corners[e]) for s, e in edges]
    trace = _segment_cylinder_trace(
        segments,
        radius=0.04,
        color="#777777",
        opacity=0.8,
        sides=4,
        name="unit-cell-box",
    )
    return [_annotate_trace(trace, "unit_cell")] if trace is not None else []


def hull_mesh_trace(shell_coords, color: str, opacity: float = 0.15, hull: dict | None = None):
    coords = np.array(shell_coords, dtype=float)
    if len(coords) < 4:
        return None
    simplices = _hull_simplices(coords, hull or {})
    if len(simplices) == 0:
        return None
    return go.Mesh3d(
        x=coords[:, 0],
        y=coords[:, 1],
        z=coords[:, 2],
        i=simplices[:, 0],
        j=simplices[:, 1],
        k=simplices[:, 2],
        color=color,
        opacity=opacity,
        flatshading=True,
        hoverinfo="skip",
        showlegend=False,
        name="coordination-hull",
    )


def _overlay_coords_and_hull(overlay) -> tuple[np.ndarray, dict]:
    if isinstance(overlay, dict):
        coords = np.asarray(overlay.get("shell_coords") or [], dtype=float)
        hull = overlay.get("hull") or {}
    else:
        coords = np.asarray(overlay or [], dtype=float)
        hull = {}
    return coords, hull


def _hull_simplices(coords: np.ndarray, hull: dict) -> np.ndarray:
    simplices = np.asarray(hull.get("simplices") or [], dtype=int)
    if simplices.ndim == 2 and simplices.shape[1] == 3:
        return simplices
    try:
        from scipy.spatial import ConvexHull
    except Exception:  # pragma: no cover - optional dependency
        return np.zeros((0, 3), dtype=int)
    try:
        return np.asarray(ConvexHull(coords).simplices, dtype=int)
    except Exception:
        return np.zeros((0, 3), dtype=int)


def _hull_edges(coords: np.ndarray, hull: dict) -> list[tuple[int, int]]:
    edges = hull.get("edges") or []
    if edges:
        return [tuple(sorted((int(edge[0]), int(edge[1])))) for edge in edges if len(edge) >= 2]
    edge_set: set[tuple[int, int]] = set()
    for simplex in _hull_simplices(coords, hull):
        a, b, c = simplex
        edge_set.add(tuple(sorted((int(a), int(b)))))
        edge_set.add(tuple(sorted((int(b), int(c)))))
        edge_set.add(tuple(sorted((int(a), int(c)))))
    return sorted(edge_set)


def hull_edge_traces(shell_coords, color: str, hull: dict | None = None):
    coords = np.array(shell_coords, dtype=float)
    if len(coords) < 4:
        return []
    edges = _hull_edges(coords, hull or {})

    segments = [(coords[i], coords[j]) for (i, j) in edges]
    # Edge thickness scales with the polyhedron itself: take a small
    # fraction of the typical edge length so a tiny ClO4 tetrahedron and
    # a large CN=12 cuboctahedron both look proportionally tubed (rather
    # than the tetrahedron looking like a ball of pipes).
    if segments:
        lengths = [float(np.linalg.norm(np.asarray(b) - np.asarray(a))) for a, b in segments]
        typical = float(np.median(lengths)) if lengths else 1.0
        radius = max(0.025, min(0.085, 0.025 * typical))
    else:
        radius = 0.04
    trace = _segment_cylinder_trace(
        segments,
        radius=radius,
        color=color,
        opacity=0.95,
        sides=5,
        name="coordination-edges",
    )
    return [trace] if trace is not None else []


def shell_center_lines(center, shell_coords):
    center = np.array(center, dtype=float)
    coords = np.array(shell_coords, dtype=float)
    if len(coords) == 0:
        return []
    raw_segments = [(center, np.asarray(point, dtype=float)) for point in coords]
    # Dash length tied to the typical bond length so the dash pattern
    # holds its visual rhythm whether we're looking at a 1.5 Å Cl-O
    # bond or a 5 Å rare-earth-O coordination radius.
    lengths = [float(np.linalg.norm(b - a)) for a, b in raw_segments]
    typical = float(np.median(lengths)) if lengths else 1.0
    dash_segments = _dashed_segments(raw_segments, dash_len=0.18 * typical, gap_len=0.12 * typical)
    trace = _segment_cylinder_trace(
        dash_segments,
        radius=max(0.018, 0.012 * typical),
        color="#6A5ACD",
        opacity=0.85,
        sides=4,
        name="coordination-lines",
    )
    return [trace] if trace is not None else []


def _world_sphere_marker_trace(centers, *, radius, color, opacity=0.9):
    """Mesh3d-based "marker" sphere set. Unlike Scatter3d markers, the
    radius is in world (Å) coordinates so the on-screen size grows when
    the camera dollies in -- i.e. the markers actually feel like part of
    the scene instead of pixel-fixed overlays."""
    centers = np.array(centers, dtype=float).reshape(-1, 3)
    if len(centers) == 0:
        return None
    payload = {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []}
    for center in centers:
        vertices, triangles = _sphere_mesh(center, float(radius), lat_steps=8, lon_steps=12)
        _append_mesh(payload, vertices, triangles)
    return go.Mesh3d(
        x=payload["x"],
        y=payload["y"],
        z=payload["z"],
        i=payload["i"],
        j=payload["j"],
        k=payload["k"],
        color=color,
        opacity=opacity,
        hoverinfo="skip",
        showlegend=False,
        flatshading=False,
    )


def shell_atom_traces(shell_coords, distances, color="#7C5CBF"):
    coords = np.array(shell_coords, dtype=float)
    if len(coords) == 0:
        return []
    dists = np.array(distances, dtype=float)
    if len(dists) == 0:
        dists = np.ones(len(coords))
    # World-coord sphere radius (Å). Closer neighbours render slightly
    # larger -- same intent as the old pixel-based marker, but the size
    # now scales with zoom because it lives in 3-D space.
    radii = 0.22 + (dists.max() - dists + 0.05) * 0.05
    payload = {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []}
    for pos, r in zip(coords, radii):
        vertices, triangles = _sphere_mesh(pos, float(r), lat_steps=8, lon_steps=12)
        _append_mesh(payload, vertices, triangles)
    return [
        go.Mesh3d(
            x=payload["x"],
            y=payload["y"],
            z=payload["z"],
            i=payload["i"],
            j=payload["j"],
            k=payload["k"],
            color=color,
            opacity=0.9,
            hoverinfo="skip",
            showlegend=False,
            flatshading=False,
        )
    ]


def _merged_hull_mesh(overlays: list[tuple[list, float]], color: str):
    """Pack any number of (shell_coords, opacity) overlays into the
    minimum number of Mesh3d traces -- one per distinct opacity value.
    With 40+ tiled polyhedra each contributing its own ConvexHull this
    drops the Plotly trace count by ~2x while keeping the same on-
    screen look (semi-transparent hulls layered front-to-back)."""
    bins: dict[float, dict] = {}
    for overlay, opacity in overlays:
        coords, hull = _overlay_coords_and_hull(overlay)
        if len(coords) < 4:
            continue
        simplices = _hull_simplices(coords, hull)
        if len(simplices) == 0:
            continue
        bin_payload = bins.setdefault(round(float(opacity), 4), {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []})
        base = len(bin_payload["x"])
        bin_payload["x"].extend(coords[:, 0].tolist())
        bin_payload["y"].extend(coords[:, 1].tolist())
        bin_payload["z"].extend(coords[:, 2].tolist())
        bin_payload["i"].extend((simplices[:, 0] + base).tolist())
        bin_payload["j"].extend((simplices[:, 1] + base).tolist())
        bin_payload["k"].extend((simplices[:, 2] + base).tolist())
    traces = []
    for opacity, payload in bins.items():
        if not payload["x"]:
            continue
        traces.append(
            go.Mesh3d(
                x=payload["x"],
                y=payload["y"],
                z=payload["z"],
                i=payload["i"],
                j=payload["j"],
                k=payload["k"],
                color=color,
                opacity=float(opacity),
                flatshading=True,
                hoverinfo="skip",
                showlegend=False,
                name="coordination-hull",
            )
        )
    return traces


def _merged_hull_edges(overlays: list, color: str):
    """All polyhedron edges in the scene packed into a single
    ``Scatter3d`` line trace using NaN-separated segments.

    The previous implementation tessellated each edge into a Mesh3d
    pentagonal-prism cylinder -- visually nicer but ~7-10x heavier in
    JSON. With many polyhedra tiled (DAP-4 has 40+ centres) the trace
    blew past 300 KB and dominated every callback round-trip. Lines
    in WebGL render in well under a millisecond and serialize to a
    fraction of the size; a non-zero ``line.width`` keeps the visual
    weight close enough to the cylinder version for the interactive
    overlay. Static publication exports that want fat tubes can opt
    back into the cylinder path via the legacy renderer if needed."""
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for overlay in overlays:
        coords, hull = _overlay_coords_and_hull(overlay)
        if len(coords) < 4:
            continue
        for i, j in _hull_edges(coords, hull):
            p0 = coords[i]
            p1 = coords[j]
            xs.extend([float(p0[0]), float(p1[0]), float("nan")])
            ys.extend([float(p0[1]), float(p1[1]), float("nan")])
            zs.extend([float(p0[2]), float(p1[2]), float("nan")])
    if not xs:
        return []
    trace = go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="lines",
        line=dict(color=color, width=4),
        opacity=0.95,
        hoverinfo="skip",
        showlegend=False,
        name="coordination-edges",
    )
    return [trace]


def _multi_spec_cache_key(topology_data: dict, fallback_color: str) -> tuple:
    """Build a hashable key for the renderer's painter caches that
    captures every per-spec colour and per-overlay instance override
    (used by callers that haven't migrated to ``spec_results``)."""
    spec_results = topology_data.get("spec_results") or []
    parts = []
    for entry in spec_results:
        overlay_overrides = []
        for overlay in entry.get("overlays") or []:
            color_override = overlay.get("color")
            if color_override is None and not overlay.get("visible", True):
                color_override = "_hidden"
            overlay_overrides.append((str(overlay.get("center_label") or ""), color_override))
        parts.append(
            (
                entry.get("spec_id") or "",
                entry.get("color") or fallback_color,
                tuple(overlay_overrides),
            )
        )
    return (tuple(parts), fallback_color)


def topology_background_traces(topology_data: dict | None, style: dict | None = None):
    """Hull mesh + edges for every overlay, painted per-spec when
    ``topology_data["spec_results"]`` is set; otherwise falls back to the
    legacy single-colour path keyed on ``style["topology_hull_color"]``.

    Designed to be added to the figure *before* the atom traces so atoms
    (especially faded minor / disorder positions) stay visible on top of
    the semi-transparent hull instead of getting washed out by Plotly's
    painter-order alpha stacking. Result is cached on the
    ``topology_data`` dict keyed on the per-spec colour tuple so
    toggling a cosmetic checkbox doesn't re-tessellate several thousand
    hull-edge cylinders for tiled polyhedra.
    """
    if not topology_data:
        return []
    style = style or {}
    fallback_color = str(style.get("topology_hull_color", "#7C5CBF"))
    cache = topology_data.setdefault("_background_dict_cache", {})
    cache_key = _multi_spec_cache_key(topology_data, fallback_color)
    if cache_key in cache:
        return cache[cache_key]
    primary_opacity = 0.22
    extra_opacity = 0.12

    traces: list = []
    spec_results = topology_data.get("spec_results") or []
    if spec_results:
        for entry in spec_results:
            spec_color = str(entry.get("color") or fallback_color)
            # Bucket overlays by the colour they will paint with: any
            # ``instance_overrides`` entry can swap a single fragment to
            # a different hue, and we want every solid colour to take
            # exactly one merged-mesh trace (otherwise the per-shape
            # cylinder counts explode for tiled polyhedra).
            overlays_by_color: dict[str, list[tuple[list, float]]] = {}
            for overlay in entry.get("overlays") or []:
                shell = overlay.get("shell_coords")
                if not shell:
                    continue
                if not overlay.get("visible", True):
                    continue
                opacity = primary_opacity if overlay.get("is_analysis_anchor") else extra_opacity
                color = str(overlay.get("color") or spec_color)
                overlays_by_color.setdefault(color, []).append((overlay, opacity))
            for color, group in overlays_by_color.items():
                traces.extend(_merged_hull_mesh(group, color=color))
                traces.extend(_merged_hull_edges([overlay for overlay, _ in group], color=color))
    else:
        # Legacy single-colour path: callers (or test fixtures) that
        # construct a topology_data dict by hand still see the original
        # behaviour, no colour table required.
        overlays_with_opacity = []
        if topology_data.get("shell_coords"):
            overlays_with_opacity.append((topology_data, primary_opacity))
        for extra in topology_data.get("extra_overlays") or []:
            if extra.get("shell_coords"):
                overlays_with_opacity.append((extra, extra_opacity))
        traces.extend(_merged_hull_mesh(overlays_with_opacity, color=fallback_color))
        traces.extend(_merged_hull_edges([overlay for overlay, _ in overlays_with_opacity], color=fallback_color))

    cache[cache_key] = [_trace_to_json_safe_dict(tr) for tr in traces]
    return cache[cache_key]


def topology_foreground_traces(topology_data: dict | None, style: dict | None = None):
    """Centre markers, connecting lines and shell-atom highlights.

    Multi-spec mode: one marker cluster per spec, coloured to match the
    hull. The single fragment marked ``is_analysis_anchor`` keeps the
    bigger orange marker and connecting lines so the user can always
    see which site owns the histogram / results panel. Falls back to
    the legacy single-colour layout when ``spec_results`` is absent.
    """
    if not topology_data:
        return []
    style = style or {}
    fallback_color = str(style.get("topology_hull_color", "#7C5CBF"))
    cache = topology_data.setdefault("_foreground_dict_cache", {})
    cache_key = _multi_spec_cache_key(topology_data, fallback_color)
    if cache_key in cache:
        return cache[cache_key]

    traces: list = []
    spec_results = topology_data.get("spec_results") or []
    primary_center = topology_data.get("center_coords")
    primary_coords = topology_data.get("shell_coords") or []
    primary_distances = topology_data.get("distances") or []
    # Pick the colour the analysis anchor's spec uses, so the
    # distance-coloured shell-atom markers blend with their hull.
    anchor_color = fallback_color
    if spec_results:
        anchor_spec_id = topology_data.get("analysis_spec_id")
        for entry in spec_results:
            if entry.get("spec_id") == anchor_spec_id:
                anchor_color = str(entry.get("color") or fallback_color)
                break
        else:
            anchor_color = str(spec_results[0].get("color") or fallback_color)

    if primary_center is not None and len(primary_coords) > 0:
        traces.extend(shell_center_lines(primary_center, primary_coords))
        primary_marker = _world_sphere_marker_trace(
            [primary_center],
            radius=0.55,
            color="#E07C24",
            opacity=0.95,
        )
        if primary_marker is not None:
            traces.append(primary_marker)
        if primary_distances:
            traces.extend(shell_atom_traces(primary_coords, primary_distances, color=anchor_color))

    if spec_results:
        # One faint marker cluster per spec covering its non-anchor
        # overlays. We skip the anchor centre because it already has
        # the bright orange marker above. ``instance_overrides`` may
        # paint individual fragments a different colour or hide them
        # entirely; we honour those here so the centre marker matches
        # the hull beneath.
        for entry in spec_results:
            spec_color = str(entry.get("color") or fallback_color)
            centers_by_color: dict[str, list[list[float]]] = {}
            for overlay in entry.get("overlays") or []:
                if overlay.get("is_analysis_anchor"):
                    continue
                if not overlay.get("visible", True):
                    continue
                center = overlay.get("center_coords")
                coords = overlay.get("shell_coords") or []
                if center is None or len(coords) == 0:
                    continue
                color = str(overlay.get("color") or spec_color)
                centers_by_color.setdefault(color, []).append(center)
            for color, centers in centers_by_color.items():
                extra_marker = _world_sphere_marker_trace(
                    centers,
                    radius=0.32,
                    color=color,
                    opacity=0.55,
                )
                if extra_marker is not None:
                    traces.append(extra_marker)
    else:
        extra_centers = []
        for extra in topology_data.get("extra_overlays") or []:
            center = extra.get("center_coords")
            coords = extra.get("shell_coords") or []
            if center is None or len(coords) == 0:
                continue
            extra_centers.append(center)
        if extra_centers:
            extra_marker = _world_sphere_marker_trace(
                extra_centers,
                radius=0.32,
                color=fallback_color,
                opacity=0.55,
            )
            if extra_marker is not None:
                traces.append(extra_marker)

    cache[cache_key] = [_trace_to_json_safe_dict(tr) for tr in traces]
    return cache[cache_key]


def topology_traces(topology_data: dict | None, style: dict | None = None):
    """Backwards-compatible wrapper -- background hull then foreground
    markers in a single trace list. Prefer the split helpers in
    ``build_figure`` so the painter order interleaves correctly with
    the atom mesh."""
    return [
        *topology_background_traces(topology_data, style),
        *topology_foreground_traces(topology_data, style),
    ]


def topology_histogram_figure(topology_data: dict | None) -> go.Figure:
    fig = go.Figure()
    distances = (topology_data or {}).get("all_distances", [])
    shell = set((topology_data or {}).get("distances", []))
    if distances:
        colors = ["#7C5CBF" if dist in shell else "#C9C9E8" for dist in distances]
        fig.add_trace(go.Bar(x=list(range(1, len(distances) + 1)), y=distances, marker_color=colors))
    fig.update_layout(
        margin=dict(l=18, r=18, t=28, b=28),
        paper_bgcolor="white",
        plot_bgcolor="white",
        xaxis_title="Neighbor rank",
        yaxis_title="Distance (Å)",
        showlegend=False,
        title="Distance Histogram",
    )
    return fig


def topology_results_markdown(topology_data: dict | None) -> str:
    if not topology_data:
        return (
            "Topology analysis inactive.\n"
            "Either no fragment of the requested type exists in this structure, "
            "or the topology overlay is disabled."
        )
    shape = topology_data.get("shape") or {}
    planarity = topology_data.get("planarity", {})
    prism = topology_data.get("prism_analysis", {})
    cn = int(topology_data.get("coordination_number", 0) or 0)
    gap_info = topology_data.get("gap_info") or {}
    gap = gap_info.get("gap_value")
    primary_gap_cn = gap_info.get("primary_gap_cn")
    enclosed = bool(gap_info.get("enclosed"))
    enclosure_expanded = bool(gap_info.get("enclosure_expanded"))
    shell = topology_data.get("shell") or []
    center_formula = topology_data.get("center_formula") or topology_data.get("center_species")
    center_descriptor = center_formula or topology_data.get("center_type", "?")
    lines = [
        f"Center: {topology_data.get('center_label', '?')} ({center_descriptor})",
        f"CN: {cn}" + (f"   |   gap = {gap:.3f} \u00c5" if gap is not None else ""),
    ]
    if enclosure_expanded and primary_gap_cn is not None:
        lines.append(
            f"  (gap-only CN was {primary_gap_cn}; expanded so the hull "
            "actually wraps the centre — XYn requires X inside the Y cage.)"
        )
    elif not enclosed and cn >= 4:
        lines.append(
            "  \u26a0 hull does not enclose centre even at the cutoff. "
            "Consider raising the search cutoff or treat this as a partial shell."
        )
    if shell:
        neighbours = ", ".join(
            f"{atom.get('label', '?')}({atom.get('species', '?')}) "
            f"d={atom.get('distance', 0):.2f}"
            for atom in shell[:cn or len(shell)]
        )
        lines.append(f"Shell: {neighbours}")
    label = shape.get("primary_label")
    modifier = shape.get("label_modifier")
    cshm_value = shape.get("cshm_value")
    if label:
        modifier_text = f"{modifier} " if modifier else ""
        cshm_text = f"  (CShM = {cshm_value:.2f})" if cshm_value is not None else ""
        lines.append(f"Shape: {modifier_text}{label}{cshm_text}")
        description = shape.get("structural_description") or ""
        residuals = shape.get("residuals") or []
        # Only print the verbose structural description when it adds info
        # beyond the headline label (i.e. when ``classify_shell`` had to
        # peel off residual atoms to fit a smaller core polyhedron).
        if residuals and description:
            lines.append(f"  {description}")
    elif cn:
        lines.append(
            f"No ideal-polyhedron reference for CN={cn} "
            "(shape registry covers CN 4\u201312)."
        )
    if planarity.get("best_rms") is not None:
        rms = planarity["best_rms"]
        warn = ""
        # A real octahedron / square plane has 4-coplanar RMS << 0.1 Å. Anything
        # above ~0.5 Å says "this isn't a real coordination polyhedron", which is
        # exactly the false-positive the misclassified-B-site bug used to surface.
        if rms > 0.5:
            warn = "  \u26a0 large \u2014 shell may not be a real coordination polyhedron"
        lines.append(f"Best planarity RMS: {rms:.3f} \u00c5{warn}")
    if prism.get("classification"):
        lines.append(
            f"Prism test: {prism['classification']} ({prism['twist_deg']:.1f}\u00b0)"
        )
    return "\n".join(lines)


def _traces_to_dicts(traces) -> list[dict]:
    """Materialise a list of plotly trace objects (or pre-built dicts)
    into raw dicts so we can pass them to ``go.Figure(data=...)`` as a
    single batch instead of going through ``add_trace`` one at a time."""
    out = []
    for tr in traces:
        if isinstance(tr, dict):
            out.append(_round_coord_arrays(dict(tr)))
        else:
            out.append(_round_coord_arrays(tr.to_plotly_json()))
    return out


def _trace_to_json_safe_dict(trace) -> dict:
    payload = trace if isinstance(trace, dict) else trace.to_plotly_json()
    return _round_coord_arrays(_json_safe_plotly(payload))


def _json_safe_plotly(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, dict):
        return {key: _json_safe_plotly(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe_plotly(item) for item in value]
    return value


# 0.001 Å (0.1 pm) is two orders of magnitude below the smallest atomic
# feature anyone visualises in this app, so rounding mesh vertex / line
# coordinates here is a free win on payload size: full f64 stringifies
# to ~17 chars, three-decimal floats to ~6, ie ~3x smaller for any
# vertex-heavy trace (atoms, bonds, hull edges, polyhedron meshes).
_COORD_KEYS = ("x", "y", "z")
_COORD_ROUND_DECIMALS = 3
_INDEX_KEYS = ("i", "j", "k")


def _round_coord_arrays(trace_dict: dict) -> dict:
    """Quantise vertex coordinates of a Plotly trace dict in place and
    return it.

    Three things happen here, all aimed at trimming the figure JSON
    Dash ships to the browser on every ``update_view``:

    1. Plain Python lists of floats get rounded to ``_COORD_ROUND_DECIMALS``
       (only really shrinks JSON-encoded coords, since ``17 chars -> ~6``).
    2. Numpy float arrays get downcast to ``float32``. Plotly serialises
       numpy arrays as base64 ``bdata``; ``f8`` doubles take 8 B / coord,
       ``f4`` floats take 4 B -- a flat 50% cut on every Mesh3d vertex
       array. Three-decimal rounding (~1 mÅ) is well inside f32's ~7
       significant decimal digits, so this is lossless w.r.t. the
       quantisation step (and far below any visible feature).
    3. Index arrays (``i``/``j``/``k``) get cast to the smallest int
       dtype that fits, so a ~10 k-vertex sphere mesh's faces ride as
       ``i2`` (2 B) or ``i4`` instead of ``i8`` (8 B) -- another ~75%
       drop on the index payload.
    """

    for key in _COORD_KEYS:
        seq = trace_dict.get(key)
        if isinstance(seq, list) and seq and isinstance(seq[0], (int, float)):
            trace_dict[key] = [
                round(float(v), _COORD_ROUND_DECIMALS) if isinstance(v, (int, float)) else v
                for v in seq
            ]
        elif isinstance(seq, np.ndarray) and seq.dtype.kind == "f" and seq.dtype.itemsize > 4:
            trace_dict[key] = np.ascontiguousarray(seq, dtype=np.float32)
    for key in _INDEX_KEYS:
        seq = trace_dict.get(key)
        if isinstance(seq, list) and seq and isinstance(seq[0], (int, float)):
            trace_dict[key] = [int(v) for v in seq]
        elif isinstance(seq, np.ndarray) and seq.dtype.kind in ("i", "u"):
            n = int(seq.max(initial=0)) + 1
            if n < 32_768:
                target = np.int16
            elif n < 2_147_483_648:
                target = np.int32
            else:
                target = seq.dtype
            if seq.dtype != target:
                trace_dict[key] = np.ascontiguousarray(seq, dtype=target)
    return trace_dict


def _hashable_selector(selector: dict | None) -> tuple:
    if not isinstance(selector, dict):
        return ()
    items = []
    for key in sorted(selector.keys()):
        value = selector[key]
        if isinstance(value, list):
            items.append((key, tuple(value)))
        else:
            items.append((key, value))
    return tuple(items)


def _atom_groups_cache_key(atom_groups: list[dict] | None) -> tuple:
    """Hashable summary of atom_groups for the figure JSON cache.

    All nested lists are flattened to tuples so the resulting key
    survives use as a dict key (Python lists are unhashable).
    """
    if not atom_groups:
        return ()
    return tuple(
        (
            str(g.get("id", "")),
            _hashable_selector(g.get("selector")),
            str(g.get("color") or ""),
            str(g.get("color_light") or ""),
            bool(g.get("visible", True)),
            str(g.get("material") or ""),
            str(g.get("style") or ""),
        )
        for g in atom_groups
    )


def _bond_groups_cache_key(bond_groups: list[dict] | None) -> tuple:
    """Hashable summary of bond_groups for the figure JSON cache.

    Mirrors :func:`_atom_groups_cache_key`. Editing or reordering a
    bond group must reliably re-render; if you add a new field to
    :func:`crystal_viewer.bond_groups.tag_bonds_with_groups`, extend
    this key so the cache slot doesn't go stale.
    """
    if not bond_groups:
        return ()
    return tuple(
        (
            str(g.get("id", "")),
            _hashable_selector(g.get("selector")),
            str(g.get("color") or ""),
            bool(g.get("visible", True)),
            float(g.get("radius_scale")) if g.get("radius_scale") is not None else None,
        )
        for g in bond_groups
    )


def _atom_subscene(scene: dict, sub_atoms: list[dict]) -> dict:
    """Shallow copy of ``scene`` with ``draw_atoms`` replaced. Other
    fields (``bonds``, ``M``, ``label_items``, ...) keep the original
    references; the per-partition atom builders only ever read
    ``draw_atoms`` so this is safe."""
    sub = dict(scene)
    sub["draw_atoms"] = sub_atoms
    sub.pop("_mesh_trace_cache", None)
    return sub


def _atom_traces_for_partition(
    sub_scene: dict,
    sub_style: dict,
    *,
    use_fast: bool,
):
    """Pick the right atom-trace builder for ``(material, style)`` and
    run it on ``sub_scene``. Used both by the default scene-style
    partition and by per-group material/style overrides."""
    sty = str(sub_style.get("style", "ball_stick"))
    if sty == "wireframe":
        return _wireframe_atom_traces(sub_scene, sub_style)
    if sty == "ortep":
        from .ortep import (
            ortep_atom_billboard_traces,
            ortep_atom_mesh_traces,
            ortep_atom_fill_traces,
        )

        is_open_ortep = (
            bool(sub_style.get("ortep_silhouette_outline", False))
            and bool(sub_style.get("ortep_octant_hatching", False))
        )
        if is_open_ortep:
            return ortep_atom_fill_traces(sub_scene, sub_style)
        return (
            ortep_atom_billboard_traces(sub_scene, sub_style)
            if use_fast
            else ortep_atom_mesh_traces(sub_scene, sub_style)
        )
    if use_fast or sub_style.get("material") == "flat":
        return _atom_scatter_traces(sub_scene, sub_style)
    return _atom_mesh_traces(sub_scene, sub_style)


def _cached_atom_bond_meshes(scene: dict, style: dict, *, use_fast: bool):
    """Cache atom + bond mesh trace dicts on the scene. Building Mesh3d
    objects (sphere tessellation + Plotly array validation) is by far the
    dominant cost when the user toggles a cosmetic checkbox like Labels
    or Axes -- but the vertex arrays themselves only depend on positions,
    `atom_scale`, `bond_radius`, `minor_opacity`, `minor_bond_scale` and
    the fast-rendering switch. Cache the list of trace dicts under that
    key and replay them on subsequent rebuilds, so toggling Labels no
    longer regenerates ~1500 sphere triangles."""
    # ``show_minor_only`` and opacity controls are now trace-property edits:
    # build the full geometry once, then hide/restyle major/minor traces on
    # replay. Keep this local so the caller's style still reflects the UI.
    style = dict(style)
    style["show_minor_only"] = False
    atom_groups = style.get("atom_groups") or []
    bond_groups = style.get("bond_groups") or []
    cache = scene.setdefault("_mesh_trace_cache", {})
    key = (
        bool(use_fast),
        str(style.get("material", "mesh")),
        str(style.get("style", "ball_stick")),
        str(style.get("disorder", "outline_rings")),
        str(style.get("ortep_mode", "")),
        str(style.get("ortep_mode_minor", "")),
        round(float(style.get("ortep_probability", 0.5)), 3),
        bool(style.get("minor_wireframe", False)),
        bool(style.get("monochrome", False)),
        round(float(style.get("atom_scale", 1.0)), 3),
        round(float(style.get("bond_radius", 0.1)), 3),
        round(float(style.get("minor_bond_scale", 0.6)), 3),
        round(float(style.get("major_opacity", 1.0)), 3),
        bool(style.get("force_minor_fade", False)),
        bool(style.get("ortep_atom_fill", False)),
        bool(style.get("ortep_silhouette_outline", False)),
        bool(style.get("ortep_octant_hatching", False)),
        str(style.get("force_bond_color", "")),
        str(style.get("ortep_atom_fill_color", "#FFFFFF")),
        _atom_groups_cache_key(atom_groups),
        _bond_groups_cache_key(bond_groups),
    )
    if key in cache:
        return cache[key]

    # Phase 4: bond_groups go through ``tag_bonds_with_groups`` BEFORE
    # ``_bond_segments`` reads the bond render fields, so any
    # _render_color / _render_visible / _render_radius_scale /
    # _render_opacity_scale set by a matching rule survives into the
    # mesh trace bucket key.
    if bond_groups:
        from .bond_groups import tag_bonds_with_groups

        original_bonds = scene["bonds"]
        scene["bonds"] = tag_bonds_with_groups(
            original_bonds,
            bond_groups,
            atoms=scene.get("draw_atoms") or [],
        )
    else:
        original_bonds = None

    # Phase 2: when atom_groups is set, decorate every atom with
    # per-render override fields, then partition by effective
    # (material, style) and run the matching trace builder per
    # partition. Bonds stay scene-level: their endpoint colours come
    # from the per-atom render colour (see ``_bond_segments``), and
    # bonds touching a hidden atom are dropped there as well, but we
    # don't fragment the bond render across partitions.
    scene_material = str(style.get("material", "mesh"))
    scene_style = str(style.get("style", "ball_stick"))
    if atom_groups:
        from .atom_groups import (
            partition_atoms_by_render_pipeline,
            tag_atoms_with_groups,
        )

        fragment_labels = scene.get("atom_fragment_labels") or None
        tagged_atoms = tag_atoms_with_groups(
            scene["draw_atoms"], atom_groups,
            scene_material=scene_material, scene_style=scene_style,
            fragment_labels=fragment_labels,
        )
        # Mutate the scene so ``_bond_segments`` (called below) sees
        # the per-atom ``_render_visible`` / ``_render_color`` flags.
        # We carefully restore the original list afterwards so cache
        # keys based on the unmutated scene id stay valid.
        original_atoms = scene["draw_atoms"]
        scene["draw_atoms"] = tagged_atoms
        try:
            partitions = partition_atoms_by_render_pipeline(
                tagged_atoms,
                scene_material=scene_material,
                scene_style=scene_style,
            )
            atom_traces = []
            for (part_material, part_style), part_atoms in partitions.items():
                sub_style = dict(style)
                sub_style["material"] = part_material
                sub_style["style"] = part_style
                # When the partition style flips to ``flat`` the
                # parent ``use_fast`` (set by the caller for the
                # scene-level material) may not match. Recompute it
                # locally so the partition actually picks the scatter
                # builder.
                part_use_fast = (
                    bool(sub_style.get("fast_rendering", False))
                    or part_material == "flat"
                    or len(part_atoms) > 2000
                )
                sub_scene = _atom_subscene(scene, part_atoms)
                atom_traces.extend(
                    _atom_traces_for_partition(sub_scene, sub_style, use_fast=part_use_fast)
                )
            # Bonds use the (mutated) scene with tagged atoms so the
            # endpoint visibility check works. Per-atom-group style
            # overrides only swing bond *style* when the rule covers
            # ALL drawn atoms uniformly -- otherwise we'd need to
            # split bonds across partitions (and pick the "right" half
            # per endpoint), which is visually ugly. The common case
            # ("flip the whole scene to ortep via an atom_group rule")
            # still works because the partition collapses to one
            # bucket whose style we read here.
            effective_bond_style = scene_style
            if len(partitions) == 1:
                only_key = next(iter(partitions))
                effective_bond_style = only_key[1]
            if effective_bond_style == "wireframe":
                bond_traces = _wireframe_bond_traces(scene, style)
            elif effective_bond_style == "ortep":
                bond_traces = (
                    _bond_scatter_traces(scene, style) if use_fast
                    else _bond_mesh_traces(scene, style)
                )
            elif use_fast:
                bond_traces = _bond_scatter_traces(scene, style)
            else:
                bond_traces = _bond_mesh_traces(scene, style)
            minor_outline = _minor_outline_traces(scene, style)
            minor_bonds = _minor_bond_wireframe_traces(scene, style)
            payload = {
                "atom_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in atom_traces],
                "bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in bond_traces],
                "minor_outline_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_outline],
                "minor_bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_bonds],
            }
            cache[key] = payload
            return payload
        finally:
            scene["draw_atoms"] = original_atoms
            if original_bonds is not None:
                scene["bonds"] = original_bonds

    if style.get("style") == "wireframe":
        atom_traces = _wireframe_atom_traces(scene, style)
        bond_traces = _wireframe_bond_traces(scene, style)
    elif style.get("style") == "ortep":
        from .ortep import (
            ortep_atom_billboard_traces,
            ortep_atom_mesh_traces,
            ortep_atom_fill_traces,
            ortep_axis_dash_traces,
            ortep_octant_shade_traces,
            ortep_octant_hatch_traces,
            ortep_silhouette_outline_traces,
        )

        # Classic ORTEP-III "open ellipsoid" mode: when the hatch octant +
        # silhouette outline are both enabled, the renderer drops every 3D
        # mesh body (atoms AND bonds) in favour of flat 2D-style lines.
        # Two reasons for this:
        #   1. Plotly's WebGL z-buffer aggressively culls Scatter3d line
        #      segments that share depth with a Mesh3d facet, occluding
        #      both the silhouette and the hatch arcs at most camera
        #      angles.  Removing the meshes removes the conflict.
        #   2. ORTEP-III publication figures are pure ink-on-paper drawings
        #      with no shading or lighting — the bonds are plain straight
        #      strokes, not shaded cylinders.  Flat scatter bonds match
        #      that convention exactly.
        is_open_ortep = (
            bool(style.get("ortep_silhouette_outline", False))
            and bool(style.get("ortep_octant_hatching", False))
        )
        if is_open_ortep:
            # Open-ellipsoid layered z-stack (far → near):
            #   1. bond strokes (flat black scatter)            ← bottom
            #   2. white atom-fill billboard disks
            #   3. hatch arcs + octant boundary arcs
            #   4. silhouette ellipse outline                    ← top
            # Each successive layer is pushed toward the camera by a small
            # ``ortep_z_lift_*`` offset so Plotly's WebGL z-buffer reliably
            # draws them in this order at every camera angle.
            bond_traces = _bond_scatter_traces(scene, style)
            atom_traces = ortep_atom_fill_traces(scene, style)
        else:
            atom_traces = (
                ortep_atom_billboard_traces(scene, style)
                if use_fast
                else ortep_atom_mesh_traces(scene, style)
            )
            bond_traces = (
                _bond_scatter_traces(scene, style) if use_fast
                else _bond_mesh_traces(scene, style)
            )
        atom_traces.extend(ortep_axis_dash_traces(scene, style))
        atom_traces.extend(ortep_octant_shade_traces(scene, style))
        atom_traces.extend(ortep_octant_hatch_traces(scene, style))
        atom_traces.extend(ortep_silhouette_outline_traces(scene, style))
    elif use_fast:
        atom_traces = _atom_scatter_traces(scene, style)
        bond_traces = _bond_scatter_traces(scene, style)
    else:
        atom_traces = _atom_mesh_traces(scene, style)
        bond_traces = _bond_mesh_traces(scene, style)
    minor_outline = _minor_outline_traces(scene, style)
    minor_bonds = _minor_bond_wireframe_traces(scene, style)
    payload = {
        "atom_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in atom_traces],
        "bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in bond_traces],
        "minor_outline_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_outline],
        "minor_bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_bonds],
    }
    cache[key] = payload
    if original_bonds is not None:
        scene["bonds"] = original_bonds
    return payload


def build_row_figure(
    scene_style_pairs: list[tuple[dict, dict]],
    bgcolor: str = "#FFFFFF",
) -> go.Figure:
    """Pack N scenes side-by-side in a 1×N Plotly subplot figure.

    Each scene gets its own 3D scene (``scene``, ``scene2``, …) with an
    independent camera and viewport.  Calling code should call
    :func:`uniform_viewport` on all scenes *before* this function so the
    rendered structures share a common physical scale.

    Parameters
    ----------
    scene_style_pairs:
        List of ``(scene_dict, style_dict)`` tuples, one per column.
    bgcolor:
        Figure and scene background colour.

    Returns
    -------
    go.Figure
        A multi-column Plotly figure ready for ``write_image``.
    """
    from plotly.subplots import make_subplots

    n = len(scene_style_pairs)
    if n == 0:
        return go.Figure()

    # Build the subplot template to get the correct domain layout.
    fig_template = make_subplots(
        rows=1, cols=n,
        specs=[[{"type": "scene"}] * n],
        horizontal_spacing=0.01,
    )
    layout_dict = fig_template.to_dict()["layout"]

    # Scene names follow Plotly's convention: scene, scene2, scene3, …
    scene_names = ["scene"] + [f"scene{i + 2}" for i in range(n - 1)]

    all_trace_dicts: list[dict] = []
    for col_idx, (scene, style) in enumerate(scene_style_pairs):
        style_norm = validate_style_schema(style)
        xr, yr, zr = _scene_ranges(scene, style_norm)
        use_fast = (
            bool(style_norm.get("fast_rendering", False))
            or style_norm.get("material") == "flat"
            or len(scene.get("draw_atoms", [])) > 2000
        )
        mesh_payload = _cached_atom_bond_meshes(scene, style_norm, use_fast=use_fast)

        # Same hidden-label propagation as build_figure.
        hidden_labels_row: set = set()
        atom_groups_row = style_norm.get("atom_groups") or []
        if atom_groups_row:
            from .atom_groups import hidden_atom_label_set, tag_atoms_with_groups

            tagged_row = tag_atoms_with_groups(scene["draw_atoms"], atom_groups_row)
            hidden_labels_row = hidden_atom_label_set(tagged_row)

        trace_dicts: list[dict] = []
        trace_dicts.extend(mesh_payload["bond_dicts"])
        trace_dicts.extend(mesh_payload["minor_bond_dicts"])
        trace_dicts.extend(mesh_payload["atom_dicts"])
        trace_dicts.extend(mesh_payload["minor_outline_dicts"])
        trace_dicts.extend(_traces_to_dicts(_contact_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_label_traces(scene, style_norm, hidden_labels=hidden_labels_row)))
        trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style_norm)))
        trace_dicts.append(
            _round_coord_arrays(_atom_selection_trace(scene, style_norm, hidden_labels=hidden_labels_row).to_plotly_json())
        )

        trace_dicts = _style_trace_dicts(trace_dicts, style_norm)
        scene_name = scene_names[col_idx]
        for td in trace_dicts:
            td["scene"] = scene_name
        all_trace_dicts.extend(trace_dicts)

        xr_span = xr[1] - xr[0]
        yr_span = yr[1] - yr[0]
        zr_span = zr[1] - zr[0]
        is_cube = (
            max(abs(xr_span - yr_span), abs(yr_span - zr_span), abs(xr_span - zr_span)) < 1e-6
        )
        aspectmode = "cube" if is_cube else "data"
        camera = _plotly_camera_from_scene(scene, style_norm)

        layout_dict[scene_name] = {
            "xaxis": {"visible": False, "range": xr},
            "yaxis": {"visible": False, "range": yr},
            "zaxis": {"visible": False, "range": zr},
            "aspectmode": aspectmode,
            "camera": camera,
            "bgcolor": bgcolor,
        }

    layout_dict.update(
        showlegend=False,
        paper_bgcolor=bgcolor,
        plot_bgcolor=bgcolor,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
    )

    fig = go.Figure(data=all_trace_dicts, layout=layout_dict, _validate=False)
    return fig


def build_figure(scene: dict, style: dict, topology_data: dict | None = None) -> go.Figure:
    style = validate_style_schema(style)
    xr, yr, zr = _scene_ranges(scene, style, topology_data=topology_data if style.get("topology_enabled", False) else None)
    # Mesh3d atoms are 3D world-coordinate spheres -- they grow when the
    # camera dollies in, which is what users expect from "zoom". Scatter3d
    # markers are pixel-fixed and break that expectation (the user reported
    # that toggling Hydrogens on PEP unit-cell suddenly produced "flat"
    # atoms because the threshold tripped). With the per-scene mesh cache
    # in place even ~700-atom scenes stay responsive on the warm path,
    # so the threshold is now ~3x looser. The explicit "Fast rendering
    # fallback" checkbox remains the user-controlled escape hatch.
    use_fast = bool(style.get("fast_rendering", False)) or style.get("material") == "flat" or len(scene.get("draw_atoms", [])) > 2000

    mesh_payload = _cached_atom_bond_meshes(scene, style, use_fast=use_fast)
    topology_on = bool(style.get("topology_enabled", False)) and topology_data is not None

    # Phase 2: derive labels of atoms hidden by atom_groups visible:false
    # so labels and the click-target overlay stay in sync with what's
    # actually drawn. The mesh cache may already have done this work --
    # but it restores ``scene["draw_atoms"]`` afterwards, so we have to
    # tag again here. The cost is one shallow-dict-per-atom decoration
    # which is well below the Plotly-validation cost we just saved.
    hidden_labels: set = set()
    atom_groups = style.get("atom_groups") or []
    if atom_groups:
        from .atom_groups import hidden_atom_label_set, tag_atoms_with_groups

        tagged = tag_atoms_with_groups(scene["draw_atoms"], atom_groups)
        hidden_labels = hidden_atom_label_set(tagged)

    # Build a flat list of trace dicts (skipping per-trace Plotly validation
    # by passing dicts straight to ``go.Figure``) instead of repeated
    # ``add_trace`` calls. ``add_trace`` re-runs the full validator chain
    # on every call -- profiling showed ~70% of warm rebuild time was in
    # that machinery alone.
    trace_dicts: list[dict] = []
    if topology_on:
        trace_dicts.extend(_traces_to_dicts(topology_background_traces(topology_data, style)))
    trace_dicts.extend(mesh_payload["bond_dicts"])
    trace_dicts.extend(mesh_payload["minor_bond_dicts"])
    trace_dicts.extend(mesh_payload["atom_dicts"])
    trace_dicts.extend(mesh_payload["minor_outline_dicts"])
    trace_dicts.extend(_traces_to_dicts(_contact_traces(scene, style)))
    # _highlight_traces (fake specular dots) are deliberately *not* added.
    # They were Scatter3d markers with pixel-fixed sizes -- in the static
    # publication path they read as ugly translucent halos that engulf the
    # atoms when the scene is zoomed out, and the proper Mesh3d shading on
    # `_atom_mesh_traces` already gives a believable highlight.
    trace_dicts.extend(_traces_to_dicts(_label_traces(scene, style, hidden_labels=hidden_labels)))
    trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style)))
    trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style)))
    if topology_on:
        trace_dicts.extend(_traces_to_dicts(topology_foreground_traces(topology_data, style)))
    trace_dicts.append(_round_coord_arrays(_atom_selection_trace(scene, style, hidden_labels=hidden_labels).to_plotly_json()))
    # Phase 4: extra invisible markers so the right-click menu has
    # click targets for polyhedron centres and bond midpoints.
    if topology_on:
        poly_pick = _polyhedron_selection_trace(topology_data)
        if poly_pick is not None:
            trace_dicts.append(_round_coord_arrays(poly_pick.to_plotly_json()))
    bond_pick = _bond_selection_trace(scene, style)
    if bond_pick is not None:
        trace_dicts.append(_round_coord_arrays(bond_pick.to_plotly_json()))

    # ``_validate=False`` skips Plotly's per-property validator chain when
    # constructing the figure. We've already validated the dicts via
    # ``to_plotly_json()`` upstream, so skipping here is safe and shaves
    # another ~50% off the warm rebuild path on small / medium scenes.
    trace_dicts = _style_trace_dicts(trace_dicts, style)
    fig = go.Figure(data=trace_dicts, _validate=False)

    show_title = bool(style.get("show_title", True))
    title_arg = dict(text=scene["title"], x=0.5) if show_title else None
    top_margin = 50 if show_title else 0

    # If all three axis ranges share a side (i.e. a caller stamped a cube via
    # uniform_viewport), lock the aspect ratio to ``cube`` so the camera does
    # not stretch when Plotly renders to a non-square viewport.
    xr_span = xr[1] - xr[0]
    yr_span = yr[1] - yr[0]
    zr_span = zr[1] - zr[0]
    is_cube = max(
        abs(xr_span - yr_span),
        abs(yr_span - zr_span),
        abs(xr_span - zr_span),
    ) < 1e-6
    aspectmode = "cube" if is_cube else "data"

    ui_revision = style.get("uirevision", str(scene.get("name", "scene")))
    layout_kwargs = dict(
        title=title_arg,
        showlegend=False,
        uirevision=ui_revision,
        paper_bgcolor=style.get("background", "#FFFFFF"),
        plot_bgcolor=style.get("background", "#FFFFFF"),
        margin=dict(l=0, r=0, t=top_margin, b=0),
        scene=dict(
            xaxis=dict(visible=False, range=xr),
            yaxis=dict(visible=False, range=yr),
            zaxis=dict(visible=False, range=zr),
            aspectmode=aspectmode,
            camera=_plotly_camera_from_scene(scene, style),
            uirevision=ui_revision,
            bgcolor=style.get("background", "#FFFFFF"),
        ),
    )
    key_annotations, key_shapes = axis_key_overlay(scene, style)
    if key_annotations:
        layout_kwargs["annotations"] = key_annotations
    if key_shapes:
        layout_kwargs["shapes"] = key_shapes
    fig.update_layout(**layout_kwargs)
    return fig
