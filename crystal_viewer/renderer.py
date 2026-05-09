from __future__ import annotations

import math
from typing import Dict, Iterable, Tuple

import numpy as np
import plotly.graph_objects as go

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


def _style_color(color: str, style: dict) -> str:
    return "#000000" if style.get("monochrome", False) else color


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
        a = np.array(scene["M"][:, 0], dtype=float)
        b = np.array(scene["M"][:, 1], dtype=float)
        c = np.array(scene["M"][:, 2], dtype=float)
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


def _atom_selection_trace(scene: dict, style: dict):
    xs, ys, zs, sizes, labels, customdata = [], [], [], [], [], []
    for idx, atom in enumerate(scene["draw_atoms"]):
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        xs.append(float(atom["cart"][0]))
        ys.append(float(atom["cart"][1]))
        zs.append(float(atom["cart"][2]))
        sizes.append(max(6.0, 48.0 * atom["atom_radius"] * float(style["atom_scale"])))
        labels.append(atom["label"])
        customdata.append([idx, atom["label"], atom["elem"], int(atom["is_minor"])])
    return go.Scatter3d(
        x=xs,
        y=ys,
        z=zs,
        mode="markers",
        marker=dict(size=sizes, color="rgba(0,0,0,0)", opacity=0.02),
        customdata=customdata,
        hovertemplate="%{customdata[1]} (%{customdata[2]})<extra></extra>",
        showlegend=False,
        name="atom-selection",
    )


def _bond_segments(scene: dict, style: dict):
    """Yield ``(color, is_minor, start, end)`` 4-tuples for every bond half.

    A ``style["force_bond_color"]`` (hex string) overrides per-atom bond
    colouring without touching any other colour in the scene.  This is the
    knob the open-ellipsoid ORTEP path uses to render every bond as plain
    black ink, matching the publication ORTEP-III convention without
    forcing ``monochrome=True`` (which would also blacken atom fills).
    """
    forced = style.get("force_bond_color")
    for bond in scene["bonds"]:
        if style.get("show_minor_only", False) and not bond["is_minor"]:
            continue
        start = np.array(bond["start"], dtype=float)
        end = np.array(bond["end"], dtype=float)
        mid = (start + end) / 2.0
        c_i = forced if forced else _style_color(bond["color_i"], style)
        c_j = forced if forced else _style_color(bond["color_j"], style)
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
                    yield color, is_minor, dash_start, dash_end
            else:
                yield color, is_minor, seg_start, seg_end


def _bond_mesh_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool], dict] = {}
    radius = max(0.04, float(style["bond_radius"]))
    for color, is_minor, start, end in _bond_segments(scene, style):
        key = (color, is_minor)
        groups.setdefault(key, {"segments": []})["segments"].append((start, end))

    traces = []
    for (color, is_minor), payload in groups.items():
        vertices, triangles = _cylinder_mesh_batch(
            payload["segments"],
            radius * (float(style.get("minor_bond_scale", 0.82)) if is_minor else 1.0),
            sides=6,
        )
        if len(vertices) == 0:
            continue
        traces.append(
            go.Mesh3d(
                x=vertices[:, 0],
                y=vertices[:, 1],
                z=vertices[:, 2],
                i=triangles[:, 0],
                j=triangles[:, 1],
                k=triangles[:, 2],
                color=color,
                opacity=_minor_opacity_for(style, is_minor),
                hoverinfo="skip",
                showlegend=False,
                flatshading=False,
            )
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
    groups: Dict[Tuple[str, bool], dict] = {}
    for atom in scene["draw_atoms"]:
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        key = (_style_color(atom["color"], style), atom["is_minor"])
        groups.setdefault(key, {"centers": [], "radii": []})
        radius = float(atom["atom_radius"]) * float(style["atom_scale"])
        if atom["is_minor"]:
            radius *= 1.12
        groups[key]["centers"].append(atom["cart"])
        groups[key]["radii"].append(radius)

    traces = []
    for (color, is_minor), payload in groups.items():
        vertices, triangles = _sphere_mesh_batch(
            payload["centers"],
            payload["radii"],
            lat_steps=lat_steps,
            lon_steps=lon_steps,
        )
        traces.append(
            go.Mesh3d(
                x=vertices[:, 0],
                y=vertices[:, 1],
                z=vertices[:, 2],
                i=triangles[:, 0],
                j=triangles[:, 1],
                k=triangles[:, 2],
                color=color,
                opacity=_minor_opacity_for(style, is_minor),
                hoverinfo="skip",
                showlegend=False,
                flatshading=False,
            )
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
            go.Scatter3d(
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
            )
        )
    return traces


def _atom_scatter_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool], dict] = {}
    for idx, atom in enumerate(scene["draw_atoms"]):
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        key = (atom["elem"], atom["is_minor"])
        groups.setdefault(
            key,
            {"x": [], "y": [], "z": [], "size": [], "text": [], "color": _style_color(atom["color"], style), "customdata": []},
        )
        base_size = max(10.0, 95.0 * atom["atom_radius"] * float(style["atom_scale"]))
        groups[key]["x"].append(float(atom["cart"][0]))
        groups[key]["y"].append(float(atom["cart"][1]))
        groups[key]["z"].append(float(atom["cart"][2]))
        groups[key]["size"].append(base_size * (1.12 if atom["is_minor"] else 1.0))
        groups[key]["text"].append(atom["label"])
        groups[key]["customdata"].append([idx, atom["label"], atom["elem"], int(atom["is_minor"])])

    traces = []
    for (elem, is_minor), payload in groups.items():
        traces.append(
            go.Scatter3d(
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
                    opacity=_minor_opacity_for(style, is_minor),
                    line=dict(color="#444444" if is_minor else payload["color"], width=3.5 if is_minor else 0),
                ),
                showlegend=False,
                name=f"{elem}{' minor' if is_minor else ''}",
            )
        )
    return traces


def _minor_bond_wireframe_traces(scene: dict, style: dict):
    if style.get("disorder") not in ("outline_rings", "dashed_bonds") and not style.get("minor_wireframe", False):
        return []
    segments = []
    for bond in scene["bonds"]:
        if not bond["is_minor"]:
            continue
        segments.append((np.array(bond["start"], dtype=float), np.array(bond["end"], dtype=float)))
    if not segments:
        return []
    if style.get("disorder") == "dashed_bonds":
        lengths = [float(np.linalg.norm(end - start)) for start, end in segments]
        typical = float(np.median(lengths)) if lengths else 1.0
        segments = _dashed_segments(
            segments,
            dash_len=max(0.08, 0.18 * typical),
            gap_len=max(0.05, 0.12 * typical),
        )
    radius = max(0.015, 0.55 * float(style["bond_radius"]))
    trace = _segment_cylinder_trace(
        segments,
        radius=radius,
        color="#202020",
        opacity=0.9,
        sides=4,
        name="minor-bond-wireframe",
    )
    return [trace] if trace is not None else []


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
        radius = max(0.05, float(atom["atom_radius"]) * float(style["atom_scale"]))
        key = (_style_color(atom["color"], style), atom["is_minor"])
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
            traces.append(trace)
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
            traces.append(trace)
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
    minors = []
    for atom in scene["draw_atoms"]:
        if not atom["is_minor"]:
            continue
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        ring_scale = 1.34 if style.get("minor_wireframe", False) else 1.20
        radius = float(atom["atom_radius"]) * float(style["atom_scale"]) * ring_scale
        minors.append((np.asarray(atom["cart"], dtype=float), radius))
    if not minors:
        return []
    color = "#111111" if style.get("minor_wireframe", False) else "#555555"
    cylinder_radius = 0.022 if style.get("minor_wireframe", False) else 0.014
    segments: list[tuple[np.ndarray, np.ndarray]] = []
    axes = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ]
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
    return [trace] if trace is not None else []


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


def _label_traces(scene: dict, style: dict):
    if not style.get("show_labels", True):
        return []
    cache = scene.setdefault("_label_trace_cache", {})
    key = (
        bool(style.get("show_minor_only", False)),
        bool(style.get("monochrome", False)),
        str(style.get("label_color", "#111111")),
        round(float(style.get("label_font_size", 12)), 3),
    )
    if key in cache:
        return cache[key]
    # Use a single font size for every atom label (was 10 vs 11 split by
    # minor-disorder flag, which read as inconsistent typography rather than
    # signalling "minor"). Disorder is conveyed by colour only; size stays uniform.
    label_size = float(style.get("label_font_size", 12))
    major_label_color = style.get("label_color", "#111111")
    buckets = {
        False: {"x": [], "y": [], "z": [], "text": [], "color": major_label_color},
        True: {"x": [], "y": [], "z": [], "text": [], "color": "#999999"},
    }
    for item in scene["label_items"]:
        if style.get("show_minor_only", False) and not item["is_minor"]:
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
            go.Scatter3d(
                x=bucket["x"],
                y=bucket["y"],
                z=bucket["z"],
                mode="text",
                text=bucket["text"],
                textfont=dict(size=label_size, color=bucket["color"]),
                hoverinfo="skip",
                showlegend=False,
            )
        )
    cache[key] = [_round_coord_arrays(tr.to_plotly_json()) for tr in traces]
    return cache[key]


def _axis_traces(scene: dict, style: dict):
    if not style.get("show_axes", True):
        return []
    mins = np.array(scene["bounds"]["mins"], dtype=float)
    screen_span = max(scene["bounds"]["screen_ranges"])
    offset = 0.10 * screen_span
    origin = mins - offset * np.array(scene["view_x"], dtype=float)
    origin -= offset * np.array(scene["view_y"], dtype=float)
    scale = float(style["axis_scale"]) * screen_span
    color = style.get("axis_color", "#666666")
    opacity = float(style.get("axis_opacity", 0.72))
    labels = style.get("axes_labels") or ["a", "b", "c"]
    labels = list(labels) + ["", "", ""]  # pad defensively

    # Match thickness to the legend size so the axis shafts stay
    # proportional to the structure they annotate, regardless of zoom.
    shaft_radius = max(0.025, 0.012 * scale)

    segments: list[tuple[np.ndarray, np.ndarray]] = []
    label_positions: list[tuple[np.ndarray, str]] = []
    for vec, label in zip(
        [scene["M"][:, 0], scene["M"][:, 1], scene["M"][:, 2]],
        labels[:3],
    ):
        v = _normalize(vec, [1.0, 0.0, 0.0])
        end = origin + v * scale
        segments.append((origin, end))
        label_positions.append((end, label))

    traces: list = []
    shaft = _segment_cylinder_trace(
        segments,
        radius=shaft_radius,
        color=color,
        opacity=opacity,
        sides=5,
        name="axes-shafts",
    )
    if shaft is not None:
        traces.append(shaft)
    if label_positions:
        traces.append(
            go.Scatter3d(
                x=[float(p[0]) for p, _ in label_positions],
                y=[float(p[1]) for p, _ in label_positions],
                z=[float(p[2]) for p, _ in label_positions],
                mode="text",
                text=[lab for _, lab in label_positions],
                textfont=dict(size=12, color=color),
                hoverinfo="skip",
                showlegend=False,
            )
        )
    return traces


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

    Set ``style["show_axis_key"] = True`` to include the triad; when off this
    helper returns empty lists. The projections are read from
    ``scene["projected_axes"]`` (populated by :func:`scene.build_scene_from_atoms`)
    and the label strings come from ``style["axes_labels"]`` with stacking
    order controlled by ``style["axis_key_label_order"]``.
    """
    if not style.get("show_axis_key", False):
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
    if not style.get("show_unit_cell", False):
        return []
    origin = np.zeros(3, dtype=float)
    a = np.array(scene["M"][:, 0], dtype=float)
    b = np.array(scene["M"][:, 1], dtype=float)
    c = np.array(scene["M"][:, 2], dtype=float)
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
    return [trace] if trace is not None else []


def hull_mesh_trace(shell_coords, color: str, opacity: float = 0.15):
    coords = np.array(shell_coords, dtype=float)
    if len(coords) < 4:
        return None
    try:
        from scipy.spatial import ConvexHull
    except Exception:  # pragma: no cover - optional dependency
        return None
    hull = ConvexHull(coords)
    return go.Mesh3d(
        x=coords[:, 0],
        y=coords[:, 1],
        z=coords[:, 2],
        i=hull.simplices[:, 0],
        j=hull.simplices[:, 1],
        k=hull.simplices[:, 2],
        color=color,
        opacity=opacity,
        flatshading=True,
        hoverinfo="skip",
        showlegend=False,
        name="coordination-hull",
    )


def hull_edge_traces(shell_coords, color: str):
    coords = np.array(shell_coords, dtype=float)
    if len(coords) < 4:
        return []
    try:
        from scipy.spatial import ConvexHull
    except Exception:  # pragma: no cover - optional dependency
        return []
    hull = ConvexHull(coords)
    edges = set()
    for simplex in hull.simplices:
        a, b, c = simplex
        edges.add(tuple(sorted((int(a), int(b)))))
        edges.add(tuple(sorted((int(b), int(c)))))
        edges.add(tuple(sorted((int(a), int(c)))))

    segments = [(coords[i], coords[j]) for (i, j) in sorted(edges)]
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
    try:
        from scipy.spatial import ConvexHull
    except Exception:  # pragma: no cover - optional dep
        return []
    bins: dict[float, dict] = {}
    for coords, opacity in overlays:
        coords = np.asarray(coords, dtype=float)
        if len(coords) < 4:
            continue
        try:
            hull = ConvexHull(coords)
        except Exception:
            continue
        bin_payload = bins.setdefault(round(float(opacity), 4), {"x": [], "y": [], "z": [], "i": [], "j": [], "k": []})
        base = len(bin_payload["x"])
        bin_payload["x"].extend(coords[:, 0].tolist())
        bin_payload["y"].extend(coords[:, 1].tolist())
        bin_payload["z"].extend(coords[:, 2].tolist())
        bin_payload["i"].extend((hull.simplices[:, 0] + base).tolist())
        bin_payload["j"].extend((hull.simplices[:, 1] + base).tolist())
        bin_payload["k"].extend((hull.simplices[:, 2] + base).tolist())
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


def _merged_hull_edges(overlays: list[list], color: str):
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
    try:
        from scipy.spatial import ConvexHull
    except Exception:  # pragma: no cover - optional dep
        return []
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for coords in overlays:
        coords = np.asarray(coords, dtype=float)
        if len(coords) < 4:
            continue
        try:
            hull = ConvexHull(coords)
        except Exception:
            continue
        edges: set[tuple[int, int]] = set()
        for simplex in hull.simplices:
            a, b, c = simplex
            edges.add(tuple(sorted((int(a), int(b)))))
            edges.add(tuple(sorted((int(b), int(c)))))
            edges.add(tuple(sorted((int(a), int(c)))))
        for i, j in edges:
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


def topology_background_traces(topology_data: dict | None, style: dict | None = None):
    """Hull mesh + edges for every overlay. Designed to be added to the
    figure *before* the atom traces so atoms (especially faded minor /
    disorder positions) stay visible on top of the semi-transparent hull
    instead of getting washed out by Plotly's painter-order alpha
    stacking. Result is cached on the ``topology_data`` dict keyed on
    ``hull_color`` so toggling a cosmetic checkbox doesn't re-tessellate
    several thousand hull-edge cylinders for tiled polyhedra."""
    if not topology_data:
        return []
    style = style or {}
    hull_color = str(style.get("topology_hull_color", "#7C5CBF"))
    cache = topology_data.setdefault("_background_dict_cache", {})
    if hull_color in cache:
        return cache[hull_color]
    primary_opacity = 0.22
    extra_opacity = 0.12

    overlays_with_opacity: list[tuple[list, float]] = []
    if topology_data.get("shell_coords"):
        overlays_with_opacity.append((topology_data["shell_coords"], primary_opacity))
    for extra in topology_data.get("extra_overlays") or []:
        if extra.get("shell_coords"):
            overlays_with_opacity.append((extra["shell_coords"], extra_opacity))

    traces = list(_merged_hull_mesh(overlays_with_opacity, color=hull_color))
    traces.extend(_merged_hull_edges([c for c, _ in overlays_with_opacity], color=hull_color))
    cache[hull_color] = [_trace_to_json_safe_dict(tr) for tr in traces]
    return cache[hull_color]


def topology_foreground_traces(topology_data: dict | None, style: dict | None = None):
    """Center markers, connecting lines and shell-atom highlights for the
    primary overlay plus a faint dot per extra overlay. These belong on
    top of the atom traces so the user can always see which site owns
    the histogram / results panel. Cached on ``topology_data`` keyed on
    ``hull_color``."""
    if not topology_data:
        return []
    style = style or {}
    hull_color = str(style.get("topology_hull_color", "#7C5CBF"))
    cache = topology_data.setdefault("_foreground_dict_cache", {})
    if hull_color in cache:
        return cache[hull_color]

    traces: list = []
    primary_center = topology_data.get("center_coords")
    primary_coords = topology_data.get("shell_coords") or []
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
        if topology_data.get("distances"):
            traces.extend(shell_atom_traces(primary_coords, topology_data["distances"], color=hull_color))
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
            color=hull_color,
            opacity=0.55,
        )
        if extra_marker is not None:
            traces.append(extra_marker)
    cache[hull_color] = [_trace_to_json_safe_dict(tr) for tr in traces]
    return cache[hull_color]


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
    angular = topology_data.get("angular", {})
    best = angular.get("best_match")
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
    if best:
        rmsd = best.get("angular_rmsd")
        rmsd_str = f"{rmsd:.2f}\u00b0" if rmsd is not None else "n/a"
        lines.append(f"Best ideal polyhedron: {best['name']} (angular RMSD {rmsd_str})")
    elif cn:
        if cn < 8:
            lines.append(
                f"No ideal-polyhedron reference for CN={cn} "
                "(angular library only covers CN 8\u201312)."
            )
        elif cn > 12:
            lines.append(
                f"No ideal-polyhedron reference for CN={cn} "
                "(angular library only covers CN 8\u201312)."
            )
        else:
            lines.append("No ideal polyhedron matched within the cutoff.")
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


def _cached_atom_bond_meshes(scene: dict, style: dict, *, use_fast: bool):
    """Cache atom + bond mesh trace dicts on the scene. Building Mesh3d
    objects (sphere tessellation + Plotly array validation) is by far the
    dominant cost when the user toggles a cosmetic checkbox like Labels
    or Axes -- but the vertex arrays themselves only depend on positions,
    `atom_scale`, `bond_radius`, `minor_opacity`, `minor_bond_scale` and
    the fast-rendering switch. Cache the list of trace dicts under that
    key and replay them on subsequent rebuilds, so toggling Labels no
    longer regenerates ~1500 sphere triangles."""
    cache = scene.setdefault("_mesh_trace_cache", {})
    key = (
        bool(use_fast),
        str(style.get("material", "mesh")),
        str(style.get("style", "ball_stick")),
        str(style.get("disorder", "outline_rings")),
        str(style.get("ortep_mode", "")),
        str(style.get("ortep_mode_minor", "")),
        bool(style.get("monochrome", False)),
        bool(style.get("show_minor_only", False)),
        round(float(style.get("atom_scale", 1.0)), 3),
        round(float(style.get("bond_radius", 0.1)), 3),
        round(float(style.get("minor_opacity", 0.35)), 3),
        round(float(style.get("minor_bond_scale", 0.6)), 3),
        round(float(style.get("major_opacity", 1.0)), 3),
        bool(style.get("ortep_atom_fill", False)),
        bool(style.get("ortep_silhouette_outline", False)),
        bool(style.get("ortep_octant_hatching", False)),
        str(style.get("force_bond_color", "")),
        str(style.get("ortep_atom_fill_color", "#FFFFFF")),
    )
    if key in cache:
        return cache[key]
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

        trace_dicts: list[dict] = []
        trace_dicts.extend(mesh_payload["bond_dicts"])
        trace_dicts.extend(mesh_payload["minor_bond_dicts"])
        trace_dicts.extend(mesh_payload["atom_dicts"])
        trace_dicts.extend(mesh_payload["minor_outline_dicts"])
        trace_dicts.extend(_traces_to_dicts(_contact_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_label_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style_norm)))
        trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style_norm)))
        trace_dicts.append(
            _round_coord_arrays(_atom_selection_trace(scene, style_norm).to_plotly_json())
        )

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
    xr, yr, zr = _scene_ranges(scene, style, topology_data=topology_data if style.get("topology_enabled", True) else None)
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
    topology_on = bool(style.get("topology_enabled", True)) and topology_data is not None

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
    trace_dicts.extend(_traces_to_dicts(_label_traces(scene, style)))
    trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style)))
    trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style)))
    if topology_on:
        trace_dicts.extend(_traces_to_dicts(topology_foreground_traces(topology_data, style)))
    trace_dicts.append(_round_coord_arrays(_atom_selection_trace(scene, style).to_plotly_json()))

    # ``_validate=False`` skips Plotly's per-property validator chain when
    # constructing the figure. We've already validated the dicts via
    # ``to_plotly_json()`` upstream, so skipping here is safe and shaves
    # another ~50% off the warm rebuild path on small / medium scenes.
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
