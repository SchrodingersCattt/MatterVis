from __future__ import annotations

import math
from typing import Iterable

import numpy as np


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


def cell_aspect_ratio(scene: dict) -> dict | None:
    """Return Plotly manual aspectratio from row-vector lattice lengths."""
    M = np.asarray(scene.get("M"), dtype=float) if scene.get("M") is not None else None
    if M is None or M.ndim != 2 or M.shape != (3, 3):
        return None
    lens = np.linalg.norm(M, axis=1)
    if not np.all(np.isfinite(lens)) or float(lens.max()) < 1e-9:
        return None
    lens = lens / float(lens.max())
    return {"x": float(lens[0]), "y": float(lens[1]), "z": float(lens[2])}


def _should_use_manual_cell_aspect(mode: str | None) -> bool:
    """Whether the layout should pin ``aspectmode='manual'`` to the cell ratio.

    Only ``display_mode='unit_cell'`` benefits from a lattice-locked aspect:
    the box toggle stays aspect-invariant and a long ``c`` axis renders long.
    Every other mode (``formula_unit``, ``asymmetric_unit``, ``cluster``)
    fits content first; applying the cell ratio there squishes molecules
    along anisotropic axes (SY: ``|c|=24`` vs ``|a|=|b|≈10``), which is the
    "formula 模式扁平" regression. Keep this predicate the single source of
    truth so ``figure_axis_layout`` and ``_manual_aspect_scale`` cannot drift
    apart and leave the compass projection inconsistent with the renderer.
    """
    return mode == "unit_cell"


def _manual_aspect_scale(scene: dict, style: dict, topology_data: dict | None = None) -> np.ndarray | None:
    """Return data-units per rendered cube unit for manual aspectratio.

    Plotly maps each data axis range into a rendered axis whose length is
    ``aspectratio[axis]``. A data-space vector must therefore be divided by
    ``half_range / aspectratio`` before projecting through the camera basis.
    """
    mode = style.get("display_mode", scene.get("display_mode"))
    if not _should_use_manual_cell_aspect(mode):
        return None
    aspect = cell_aspect_ratio(scene)
    if aspect is None:
        return None
    xr, yr, zr = _scene_ranges(scene, style, topology_data=topology_data)
    halves = np.array(
        [
            (float(xr[1]) - float(xr[0])) / 2.0,
            (float(yr[1]) - float(yr[0])) / 2.0,
            (float(zr[1]) - float(zr[0])) / 2.0,
        ],
        dtype=float,
    )
    ar = np.array([aspect["x"], aspect["y"], aspect["z"]], dtype=float)
    if not np.all(np.isfinite(halves)) or not np.all(halves > 0):
        return None
    return halves / np.maximum(ar, 1e-9)


def _camera_axis_projections(scene: dict, style: dict) -> list[list[float]] | None:
    """Reproject lattice axes onto the live camera's screen plane."""
    camera = style.get("camera")
    if not isinstance(camera, dict):
        return None
    eye_raw = camera.get("eye") or {}
    up_raw = camera.get("up") or {}
    center_raw = camera.get("center") or {}

    def _coerce_xyz(raw, fallback):
        try:
            if isinstance(raw, dict):
                return np.array(
                    [float(raw.get(k, fallback[i])) for i, k in enumerate(("x", "y", "z"))],
                    dtype=float,
                )
            return np.array([float(v) for v in raw], dtype=float)
        except (TypeError, ValueError):
            return None

    eye = _coerce_xyz(eye_raw, (0.0, 0.0, 1.0))
    up = _coerce_xyz(up_raw, (0.0, 1.0, 0.0))
    center = _coerce_xyz(center_raw, (0.0, 0.0, 0.0))
    if eye is None or up is None or center is None:
        return None
    if eye.shape[0] != 3 or up.shape[0] != 3 or center.shape[0] != 3:
        return None
    canonical = {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
    }
    try:
        from .compass import camera_screen_basis

        right, screen_up = camera_screen_basis(canonical)
    except (ValueError, KeyError, TypeError):
        return None

    M = np.asarray(scene.get("M"), dtype=float)
    if M.ndim != 2 or M.shape[0] < 3 or M.shape[1] != 3:
        return None

    cube_scale = _axis_cube_scale(scene, style)
    M_cube = M[:3] / cube_scale[None, :] if cube_scale is not None else M[:3]
    return [
        [float(np.dot(M_cube[i], right)), float(np.dot(M_cube[i], screen_up))]
        for i in range(3)
    ]


def _axis_cube_scale(scene: dict, style: dict) -> np.ndarray | None:
    """Return per-axis data units per rendered cube unit.

    Manual aspectratio scenes use the same range/aspect mapping as Plotly.
    Legacy data/cube scenes fall back to the historical bounds-derived scale.
    """
    manual = _manual_aspect_scale(scene, style)
    if manual is not None:
        return manual

    override = scene.get("viewport")
    if override:
        try:
            xs = (float(override["x"][1]) - float(override["x"][0])) / 2.0
            ys = (float(override["y"][1]) - float(override["y"][0])) / 2.0
            zs = (float(override["z"][1]) - float(override["z"][0])) / 2.0
        except (KeyError, TypeError, ValueError):
            return None
        return np.array([max(xs, 1e-9), max(ys, 1e-9), max(zs, 1e-9)], dtype=float)

    bounds = scene.get("bounds")
    if isinstance(bounds, dict):
        mins = bounds.get("mins")
        maxs = bounds.get("maxs")
        if mins is not None and maxs is not None:
            mins_arr = np.asarray(mins, dtype=float)
            maxs_arr = np.asarray(maxs, dtype=float)
            if mins_arr.shape == (3,) and maxs_arr.shape == (3,):
                halves = (maxs_arr - mins_arr) / 2.0
                if (halves > 0).all():
                    if float(halves.max() - halves.min()) < 1e-6:
                        return None
                    return halves
    return None


def _visible_atoms(scene: dict, style: dict):
    atoms = scene["draw_atoms"]
    if style.get("show_minor_only", False):
        atoms = [atom for atom in atoms if atom["is_minor"]]
    return atoms or scene["draw_atoms"]


def _scene_ranges(scene: dict, style: dict, topology_data: dict | None = None):
    """Compute ``[xr, yr, zr]`` axis ranges for the Plotly scene.

    In ``unit_cell`` mode the scene cube must enclose the full lattice (and
    every polyhedron drawn inside it), so cell corners + topology overlays
    are folded into the bounds. In every other mode (``formula_unit``,
    ``asymmetric_unit``, ``cluster``) the *atoms* own the scene cube — the
    cell box and ``extra_overlays`` (other formula-unit replicas of the
    chosen polyhedron) are visualization aids that may extend across the
    whole cell, but allowing them to grow the cube turns a 10 Å cluster
    into a tiny dot in a 24 Å scene. That is exactly the "Reset 后又拉长"
    regression: Reset bumps the camera revision, so Plotly applies the
    new (oversized) layout cube instead of the user's manually re-fit
    rotation, and the molecule looks comparatively flat / pushed-aside.
    Only the on-focus topology center + shell are folded in, since they
    sit on the atoms anyway.
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

    mode = style.get("display_mode", scene.get("display_mode"))
    cell_owns_cube = mode == "unit_cell"

    extras = []
    if cell_owns_cube and style.get("show_unit_cell", False) and scene.get("M") is not None:
        M = np.asarray(scene.get("M"), dtype=float)
        if M.ndim == 2 and M.shape[0] >= 3 and M.shape[1] == 3:
            a = np.array(M[0], dtype=float)
            b = np.array(M[1], dtype=float)
            c = np.array(M[2], dtype=float)
            for corner in (
                np.zeros(3, dtype=float),
                a,
                b,
                c,
                a + b,
                a + c,
                b + c,
                a + b + c,
            ):
                extras.append(corner)
    if topology_data:
        center = topology_data.get("center_coords")
        if center is not None:
            extras.append(np.array(center, dtype=float))
        for point in topology_data.get("shell_coords") or []:
            extras.append(np.array(point, dtype=float))
        if cell_owns_cube:
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
    pad = np.maximum(span * 0.06, 0.25)
    mins = atom_mins - pad
    maxs = atom_maxs + pad
    return [
        [float(mins[0]), float(maxs[0])],
        [float(mins[1]), float(maxs[1])],
        [float(mins[2]), float(maxs[2])],
    ]


def _equalize_axis_ranges(xr, yr, zr):
    """Pad each axis range to the longest span, centred on its midpoint.

    The molecular cluster in ``formula_unit`` / ``asymmetric_unit`` mode is
    roughly equiaxed, but ``show_unit_cell=True`` adds the eight cell corners
    to ``_scene_ranges``. On a long cell (SY: |b|=24 vs |a|=8) this turns the
    bounding box anisotropic, and ``aspectmode='data'`` then renders the
    scene cube anisotropic — squishing the molecules along the long axis.
    Pin a cube-shaped scene cube so the unit-cell box renders at its true
    proportions inside an otherwise undeformed viewport, and molecules keep
    their natural 1:1:1 shape regardless of the box toggle. The renderer is
    free to clip / leave whitespace; nothing relies on the range being tight.
    """
    spans = [
        float(xr[1] - xr[0]),
        float(yr[1] - yr[0]),
        float(zr[1] - zr[0]),
    ]
    max_span = max(spans)
    if not math.isfinite(max_span) or max_span <= 0:
        return list(xr), list(yr), list(zr)
    out = []
    for axis_range in (xr, yr, zr):
        a, b = float(axis_range[0]), float(axis_range[1])
        center = 0.5 * (a + b)
        half = 0.5 * max_span
        out.append([center - half, center + half])
    return out[0], out[1], out[2]


def figure_axis_layout(scene: dict, style: dict, xr, yr, zr) -> dict:
    """Build the Plotly ``scene`` layout with stable lattice aspect."""
    aspect = cell_aspect_ratio(scene)
    mode = style.get("display_mode", scene.get("display_mode"))
    if aspect is not None and _should_use_manual_cell_aspect(mode):
        aspect_kwargs = {"aspectmode": "manual", "aspectratio": aspect}
    else:
        # Equalise per-axis ranges so the rendered scene cube is 1:1:1
        # regardless of whether the unit-cell box is enabled. Without this,
        # ``show_unit_cell=True`` in ``formula_unit`` mode would widen the
        # ranges to the cell corners (e.g. SY's 8:24:10 box) and
        # ``aspectmode='cube'`` / ``'data'`` would then stretch every atom
        # along the long axis — exactly the "勾上 box 又扁" regression.
        xr, yr, zr = _equalize_axis_ranges(xr, yr, zr)
        aspect_kwargs = {"aspectmode": "cube"}

    return {
        "xaxis": {"visible": False, "range": xr},
        "yaxis": {"visible": False, "range": yr},
        "zaxis": {"visible": False, "range": zr},
        "camera": _plotly_camera_from_scene(scene, style),
        "uirevision": style.get("uirevision", str(scene.get("name", "scene"))),
        "bgcolor": style.get("background", "#FFFFFF"),
        **aspect_kwargs,
    }


def uniform_viewport(scenes, *, style=None, padding=0.0):
    """Stamp a shared world-cube viewport on scenes for equal panel scale."""
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
