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
    """Return normalized row-vector lattice lengths.

    This is a lattice summary helper. The renderer's no-flattening contract is
    based on final Cartesian axis ranges, because Plotly scales Cartesian x/y/z
    axes rather than lattice-vector directions.
    """
    M = np.asarray(scene.get("M"), dtype=float) if scene.get("M") is not None else None
    if M is None or M.ndim != 2 or M.shape != (3, 3):
        return None
    lens = np.linalg.norm(M, axis=1)
    if not np.all(np.isfinite(lens)) or float(lens.max()) < 1e-9:
        return None
    lens = lens / float(lens.max())
    return {"x": float(lens[0]), "y": float(lens[1]), "z": float(lens[2])}


def _range_aspect_ratio(xr, yr, zr) -> dict | None:
    """Return a manual aspectratio that preserves Cartesian data-unit scale."""
    try:
        spans = np.array(
            [
                float(xr[1]) - float(xr[0]),
                float(yr[1]) - float(yr[0]),
                float(zr[1]) - float(zr[0]),
            ],
            dtype=float,
        )
    except (TypeError, ValueError, IndexError):
        return None
    if not np.all(np.isfinite(spans)) or float(spans.max()) < 1e-9:
        return None
    scaled = spans / float(spans.max())
    return {"x": float(scaled[0]), "y": float(scaled[1]), "z": float(scaled[2])}


def _should_use_manual_range_aspect(mode: str | None) -> bool:
    """Whether layout should write a manual isometric range aspect.

    Only ``display_mode='unit_cell'`` needs anisotropic screen axes: the user
    expects the whole cell viewport to stay visible. The aspect components must
    come from the final Cartesian ranges, not lattice-vector norms, because
    Plotly scales the Cartesian x/y/z axes.
    Every other mode (``formula_unit``, ``asymmetric_unit``, ``cluster``)
    is made isometric by equalizing ranges and using ``aspectmode='cube'``.
    Keep this predicate the single source of truth so ``figure_axis_layout`` and
    ``_manual_aspect_scale`` cannot drift apart and leave the compass
    projection inconsistent with the renderer.
    """
    return mode == "unit_cell"


def _manual_aspect_scale(scene: dict, style: dict, topology_data: dict | None = None) -> np.ndarray | None:
    """Return data-units per rendered cube unit for manual aspectratio.

    Plotly maps each data axis range into a rendered axis whose length is
    ``aspectratio[axis]``. A data-space vector must therefore be divided by
    ``half_range / aspectratio`` before projecting through the camera basis.
    """
    mode = style.get("display_mode", scene.get("display_mode"))
    if not _should_use_manual_range_aspect(mode):
        return None
    xr, yr, zr = _scene_ranges(scene, style, topology_data=topology_data)
    aspect = _range_aspect_ratio(xr, yr, zr)
    if aspect is None:
        return None
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
    """Reproject unit lattice-basis directions onto the camera screen plane."""
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
        from ..compass import camera_screen_basis

        right, screen_up = camera_screen_basis(canonical)
    except (ValueError, KeyError, TypeError):
        return None

    M = np.asarray(scene.get("M"), dtype=float)
    if M.ndim != 2 or M.shape[0] < 3 or M.shape[1] != 3:
        return None

    cube_scale = _axis_cube_scale(scene, style)
    M_cube = M[:3] / cube_scale[None, :] if cube_scale is not None else M[:3]
    norms = np.linalg.norm(M_cube, axis=1)
    if not np.all(np.isfinite(norms)) or np.any(norms < 1e-12):
        return None
    basis_dirs = M_cube / norms[:, None]
    return [
        [float(np.dot(basis_dirs[i], right)), float(np.dot(basis_dirs[i], screen_up))]
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

    When the unit-cell box is visible, the scene cube must include the full
    lattice corners regardless of display mode; otherwise ASU / formula-unit
    views draw a full wireframe and then clip it at the atom-owned viewport.

    Topology ``extra_overlays`` point at other formula-unit replicas scattered
    across the cell. Letting those grow the cube turns the focused structure
    into a tiny object in an oversized viewport, so only the focused topology
    center + shell are folded into the range.
    """
    override = scene.get("viewport")
    if override:
        return [
            [float(override["x"][0]), float(override["x"][1])],
            [float(override["y"][0]), float(override["y"][1])],
            [float(override["z"][0]), float(override["z"][1])],
        ]

    mode = style.get("display_mode", scene.get("display_mode"))
    cell_owns_cube = mode == "unit_cell"

    def _cell_corners() -> list[np.ndarray]:
        if scene.get("M") is None:
            return []
        M = np.asarray(scene.get("M"), dtype=float)
        if M.ndim != 2 or M.shape[0] < 3 or M.shape[1] != 3:
            return []
        a = np.array(M[0], dtype=float)
        b = np.array(M[1], dtype=float)
        c = np.array(M[2], dtype=float)
        return [
            np.zeros(3, dtype=float),
            a,
            b,
            c,
            a + b,
            a + c,
            b + c,
            a + b + c,
        ]

    atoms = _visible_atoms(scene, style)
    atom_scale = float(style.get("atom_scale", 1.0))

    atom_mins = None
    atom_maxs = None
    cell_corners = _cell_corners()
    if cell_owns_cube and cell_corners:
        # In unit-cell mode the viewport is anchored on the cell, not the
        # spread of complete molecular images that may be drawn to keep
        # boundary fragments chemically contiguous. Letting those outside
        # images own the range makes the actual cell collapse into a
        # thin strip (the regression pinned by
        # ``test_unit_cell_viewport_is_owned_by_cell_not_outside_complete_fragments``).
        #
        # However, "anchored on the cell" is not the same as "rigidly clipped
        # to the cell". A cation whose centroid sits in the cell will still
        # have its tail (methyl, phenyl, ...) poke a fraction of an Å past
        # the wall after molecule unwrapping; clipping the viewport at the
        # cell wall renders that tail as half-an-atom getting truncated at
        # the box edge -- the exact "很多截断" complaint on MPEP.
        #
        # Compromise: include atoms whose centre lies within ~15% of the
        # cell span past any wall (typical bond-length poke), and reject
        # anything farther out as an explicit far-replica outlier. The
        # 15% slack still keeps the x=100-outlier contract -- a 10 Å
        # cell admits ±1.5 Å and rejects everything past 11.5 Å.
        corners_arr = np.array(cell_corners, dtype=float)
        cell_min = corners_arr.min(axis=0)
        cell_max = corners_arr.max(axis=0)
        atom_mins = cell_min.copy()
        atom_maxs = cell_max.copy()
        if atoms:
            carts = np.array([atom["cart"] for atom in atoms], dtype=float)
            radii = np.array(
                [max(float(atom.get("atom_radius", 0.18)), 0.05) for atom in atoms],
                dtype=float,
            ) * atom_scale
            cell_span = np.maximum(cell_max - cell_min, 1e-6)
            slack = 0.15 * cell_span
            keep = np.all(carts >= cell_min - slack, axis=1) & np.all(
                carts <= cell_max + slack, axis=1
            )
            if keep.any():
                kept = carts[keep]
                kept_radii = radii[keep]
                atom_mins = np.minimum(
                    atom_mins, (kept - kept_radii[:, None]).min(axis=0)
                )
                atom_maxs = np.maximum(
                    atom_maxs, (kept + kept_radii[:, None]).max(axis=0)
                )
    elif atoms:
        carts = np.array([atom["cart"] for atom in atoms], dtype=float)
        radii = np.array(
            [max(float(atom.get("atom_radius", 0.18)), 0.05) for atom in atoms],
            dtype=float,
        ) * atom_scale
        atom_mins = (carts - radii[:, None]).min(axis=0)
        atom_maxs = (carts + radii[:, None]).max(axis=0)

    extras: list[np.ndarray] = []
    if style.get("show_unit_cell", False):
        extras.extend(cell_corners)

    # Gate that decides whether a given overlay's bbox should grow the
    # viewport. Use the same "near the cell" predicate the atom kept-mask
    # uses (cell + 0.15 * span slack on every axis) so that a far-replica
    # overlay whose centre sits at e.g. x=40 -- the contract pinned by
    # ``test_off_viewport_polyhedra_extras_are_not_drawn_as_clipped_edges``
    # -- can never balloon the viewport. Cluster mode falls through with
    # ``None`` and accepts every overlay; the legacy single-spec path
    # (no `spec_results`) likewise has no far-replica problem so it stays
    # unconditional.
    overlay_bounds: tuple[np.ndarray, np.ndarray] | None = None
    if cell_owns_cube and cell_corners:
        corners_arr = np.array(cell_corners, dtype=float)
        cell_min = corners_arr.min(axis=0)
        cell_max = corners_arr.max(axis=0)
        cell_span = np.maximum(cell_max - cell_min, 1e-6)
        slack = 0.15 * cell_span
        overlay_bounds = (cell_min - slack, cell_max + slack)

    def _overlay_near_cell(coords: np.ndarray) -> bool:
        if overlay_bounds is None:
            return True
        if coords.size == 0:
            return False
        ov_min = coords.min(axis=0)
        ov_max = coords.max(axis=0)
        lo, hi = overlay_bounds
        return bool(np.all(ov_max >= lo) and np.all(ov_min <= hi))

    if topology_data:
        # The analysis anchor's center + shell come first because the legacy
        # single-spec path lives there; multi-spec callers (the modern
        # ``polyhedron_specs`` table) attach every overlay (anchor + ghosts)
        # under ``spec_results[*].overlays``. Without folding *every*
        # near-cell overlay into the viewport, non-anchor polyhedra -- the
        # ClO4 tetrahedra straddling cell faces on MPEP / DAP-4 etc. --
        # can stick 1-2 angstrom past the cell + slack region and render
        # clipped at the canvas edge ("画布截断" report on MPEP after the
        # slack/intersect viewport rewrite).
        center = topology_data.get("center_coords")
        if center is not None:
            extras.append(np.array(center, dtype=float))
        for point in topology_data.get("shell_coords") or []:
            extras.append(np.array(point, dtype=float))
        for entry in topology_data.get("spec_results") or []:
            for overlay in entry.get("overlays") or []:
                if not overlay.get("visible", True):
                    continue
                ov_points = []
                ov_center = overlay.get("center_coords")
                if ov_center is not None:
                    ov_points.append(np.array(ov_center, dtype=float))
                for point in overlay.get("shell_coords") or []:
                    ov_points.append(np.array(point, dtype=float))
                if not ov_points:
                    continue
                ov_arr = np.asarray(ov_points, dtype=float)
                if ov_arr.ndim != 2 or ov_arr.shape[1] != 3:
                    continue
                if not _overlay_near_cell(ov_arr):
                    continue
                extras.extend(ov_arr)
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
    """Build the Plotly ``scene`` layout with stable Cartesian data scale."""
    mode = style.get("display_mode", scene.get("display_mode"))
    aspect = _range_aspect_ratio(xr, yr, zr)
    
    if aspect is not None and _should_use_manual_range_aspect(mode):
        aspect_kwargs = {"aspectmode": "manual", "aspectratio": aspect}
    else:
        aspect_kwargs = {"aspectmode": "data"}

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
