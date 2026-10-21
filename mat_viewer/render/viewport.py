from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np

from .radius import atom_visual_radius
from .viewport_spec import ViewportSpec, resolve_viewport, reuse_viewport  # noqa: F401


def _geometry_entity_points(scene: dict) -> list[np.ndarray]:
    """Return visible mesh vertices for viewport ownership.

    Geometry entities are already in world Cartesian coordinates.  The
    renderer validates them before building traces; this helper intentionally
    remains tolerant of malformed hand-written entries so a bad optional
    overlay cannot make the camera-range calculation crash before the useful
    contextual validation error is raised by the trace builder.
    """

    points: list[np.ndarray] = []
    entities = scene.get("geometry_entities")
    if entities is None:
        return points
    if not isinstance(entities, Iterable) or isinstance(entities, (str, bytes, Mapping)):
        return points
    for entity in entities:
        if not isinstance(entity, Mapping) or not bool(entity.get("visible", True)):
            continue
        try:
            coords = np.asarray(entity.get("vertices"), dtype=float)
        except (TypeError, ValueError):
            continue
        if coords.ndim != 2 or coords.shape[1] != 3 or len(coords) == 0:
            continue
        if not np.all(np.isfinite(coords)):
            continue
        points.extend(np.array(row, dtype=float) for row in coords)
    return points


def _display_atom_radii(atoms: list[dict], style: Mapping[str, object]) -> np.ndarray:
    """Return radii used by both traces and viewport fitting."""
    atom_scale = float(style.get("atom_scale", 1.0))
    values = []
    for atom in atoms:
        raw = max(float(atom.get("atom_radius", 0.18)), 0.05)
        element = str(atom.get("elem") or atom.get("symbol") or "C")
        values.append(
            atom_visual_radius(
                raw,
                element=element,
                representation="ball",
                atom_scale=atom_scale,
                style=style,
            )
        )
    return np.asarray(values, dtype=float)


@dataclass
class ViewportAccumulator:
    """Accumulate one shared viewport without retaining rendered scenes."""

    padding: float = 0.0
    minimum: np.ndarray = field(default_factory=lambda: np.full(3, np.inf, dtype=float))
    maximum: np.ndarray = field(
        default_factory=lambda: np.full(3, -np.inf, dtype=float)
    )
    count: int = 0

    def _update_bounds(self, minimum: np.ndarray, maximum: np.ndarray) -> None:
        if minimum.shape != (3,) or maximum.shape != (3,):
            raise ValueError("viewport bounds must be three-dimensional")
        if not np.all(np.isfinite((minimum, maximum))):
            raise ValueError("viewport bounds must be finite")
        self.minimum = np.minimum(self.minimum, minimum)
        self.maximum = np.maximum(self.maximum, maximum)
        self.count += 1

    def update(self, scene: dict, *, style: dict | None = None) -> None:
        scn_style = style if style is not None else scene.get("style") or {}
        atoms = scene.get("draw_atoms") or []
        if atoms:
            carts = np.array([atom["cart"] for atom in atoms], dtype=float)
            radii = _display_atom_radii(atoms, scn_style)
            self._update_bounds(
                (carts - radii[:, None]).min(axis=0),
                (carts + radii[:, None]).max(axis=0),
            )
        geometry_points = _geometry_entity_points(scene)
        if geometry_points:
            geometry_arr = np.asarray(geometry_points, dtype=float)
            self._update_bounds(geometry_arr.min(axis=0), geometry_arr.max(axis=0))

    def update_points(self, points: Iterable[Iterable[float]]) -> None:
        """Accumulate finite world-space points without retaining a scene."""
        values = np.asarray(list(points), dtype=float)
        if not values.size:
            return
        if values.ndim != 2 or values.shape[1] != 3:
            raise ValueError("viewport points must have shape (N, 3)")
        self._update_bounds(values.min(axis=0), values.max(axis=0))

    def fit_points(self) -> np.ndarray:
        """Return the eight corners of the accumulated padded bounds."""
        if not self.count:
            minimum = np.full(3, -0.5 - float(self.padding), dtype=float)
            maximum = np.full(3, 0.5 + float(self.padding), dtype=float)
        else:
            minimum = self.minimum - float(self.padding)
            maximum = self.maximum + float(self.padding)
        return np.asarray(
            [
                [x, y, z]
                for x in (minimum[0], maximum[0])
                for y in (minimum[1], maximum[1])
                for z in (minimum[2], maximum[2])
            ],
            dtype=float,
        )

    def viewport(self) -> dict:
        if not self.count:
            center = np.zeros(3, dtype=float)
            half = 0.5 + float(self.padding)
        else:
            center = 0.5 * (self.minimum + self.maximum)
            half = 0.5 * float(np.max(self.maximum - self.minimum)) + float(
                self.padding
            )
        return {
            "x": [float(center[0] - half), float(center[0] + half)],
            "y": [float(center[1] - half), float(center[1] + half)],
            "z": [float(center[2] - half), float(center[2] + half)],
            "center": [float(value) for value in center],
            "half_span": float(half),
        }


def _normalize(vec: Iterable[float], fallback: Iterable[float]) -> np.ndarray:
    arr = np.array(list(vec), dtype=float)
    if arr.shape != (3,) or np.linalg.norm(arr) < 1e-8:
        arr = np.array(list(fallback), dtype=float)
    norm = np.linalg.norm(arr)
    if norm < 1e-8:
        return np.array([0.0, 0.0, 1.0], dtype=float)
    return arr / norm


def _plotly_camera_from_scene(scene: dict, style: dict) -> dict:
    # A default state may intentionally leave ``style['camera']`` unset so
    # the scene-specific camera is regenerated after a display-scope change.
    # Use that scene camera for the initial perspective fit as well; otherwise
    # a unit-cell default skips the orientation-aware margin and can crop a
    # valid cell corner before the user ever interacts with the view.
    explicit = style.get("camera")
    if not isinstance(explicit, dict):
        explicit = scene.get("camera")
    if isinstance(explicit, dict):

        def _xyz_value(raw, fallback: tuple[float, float, float]) -> dict[str, float]:
            if isinstance(raw, dict):
                return {
                    axis: float(raw.get(axis, fallback[i]))
                    for i, axis in enumerate(("x", "y", "z"))
                }
            try:
                values = [float(value) for value in raw]
            except (TypeError, ValueError):
                values = list(fallback)
            if len(values) != 3:
                values = list(fallback)
            return {axis: values[i] for i, axis in enumerate(("x", "y", "z"))}

        def _xyz(group: str, fallback: tuple[float, float, float]) -> dict[str, float]:
            return _xyz_value(explicit.get(group), fallback)

        if "eye" in explicit:
            eye = _xyz("eye", (0.0, 0.0, 1.8))
            center = _xyz("center", (0.0, 0.0, 0.0))
            up = _xyz("up", (0.0, 1.0, 0.0))
        elif "position" in explicit or "focal_point" in explicit:
            position = _xyz_value(explicit.get("position"), (0.0, 0.0, 1.0))
            focal = _xyz_value(explicit.get("focal_point"), (0.0, 0.0, 0.0))
            view_direction = np.array(
                [position[axis] - focal[axis] for axis in ("x", "y", "z")],
                dtype=float,
            )
            eye_distance = float(style.get("camera_eye_distance", 1.8))
            eye_vector = _normalize(view_direction, (0.0, 0.0, 1.0)) * eye_distance
            up_raw = explicit.get("up")
            up_vector = _normalize(
                up_raw if up_raw is not None else (0.0, 1.0, 0.0),
                (0.0, 1.0, 0.0),
            )
            eye = {axis: float(eye_vector[i]) for i, axis in enumerate(("x", "y", "z"))}
            center = {"x": 0.0, "y": 0.0, "z": 0.0}
            up = {axis: float(up_vector[i]) for i, axis in enumerate(("x", "y", "z"))}
        else:
            eye = _xyz("eye", (0.0, 0.0, 1.8))
            center = _xyz("center", (0.0, 0.0, 0.0))
            up = _xyz("up", (0.0, 1.0, 0.0))

        projection = explicit.get("projection") or {}
        projection_type = (
            projection.get("type") if isinstance(projection, dict) else projection
        )
        return {
            "eye": eye,
            "center": center,
            "up": up,
            "projection": {
                "type": str(projection_type or style.get("projection", "perspective"))
            },
        }

    eye_distance = float(style.get("camera_eye_distance", 1.8))
    eye = (
        _normalize(scene.get("view_direction", [0.0, 0.0, 1.0]), [0.0, 0.0, 1.0])
        * eye_distance
    )
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
    if not np.all(np.isfinite(spans)) or np.any(spans <= 1e-9):
        return None
    scaled = spans / float(spans.max())
    return {"x": float(scaled[0]), "y": float(scaled[1]), "z": float(scaled[2])}


def flat_projected_pixel_scale(scene: dict, style: dict, *, ranges=None) -> float:
    """Return rendered pixels per Cartesian data unit for flat primitives."""
    if ranges is None:
        ranges = _scene_ranges(scene, style)
    xr, yr, zr = ranges
    spans = np.array(
        [
            float(xr[1]) - float(xr[0]),
            float(yr[1]) - float(yr[0]),
            float(zr[1]) - float(zr[0]),
        ],
        dtype=float,
    )
    if not np.all(np.isfinite(spans)) or np.any(spans <= 1e-9):
        return 47.5

    camera = _plotly_camera_from_scene(scene, style)
    try:
        from ..compass import camera_screen_basis

        right, screen_up = camera_screen_basis(camera)
    except (KeyError, TypeError, ValueError, IndexError):
        right = np.array([1.0, 0.0, 0.0], dtype=float)
        screen_up = np.array([0.0, 1.0, 0.0], dtype=float)

    aspect = _range_aspect_ratio(xr, yr, zr)
    half_ranges = spans / 2.0
    data_to_cube = (
        np.array([aspect[axis] for axis in ("x", "y", "z")], dtype=float) / half_ranges
        if aspect is not None
        else 1.0 / half_ranges
    )
    center = np.array(
        [(float(axis_range[0]) + float(axis_range[1])) / 2.0 for axis_range in ranges],
        dtype=float,
    )
    corners = np.array(
        [[xr[ix], yr[iy], zr[iz]] for ix in (0, 1) for iy in (0, 1) for iz in (0, 1)],
        dtype=float,
    )
    cube_points = (corners - center[None, :]) * data_to_cube[None, :]
    projected = np.stack([cube_points @ right, cube_points @ screen_up], axis=1)
    projected_spans = projected.max(axis=0) - projected.min(axis=0)
    if not np.all(np.isfinite(projected_spans)) or np.any(projected_spans <= 1e-9):
        return 47.5

    width = float(style.get("figure_width", style.get("axis_key_fig_width", 1024.0)))
    height = float(style.get("figure_height", style.get("axis_key_fig_height", 720.0)))
    if not np.isfinite(width) or not np.isfinite(height) or width <= 0 or height <= 0:
        return 47.5
    px_per_cube_unit = 0.90 * min(
        width / projected_spans[0], height / projected_spans[1]
    )
    projected_unit = max(
        float(np.linalg.norm(data_to_cube * right)),
        float(np.linalg.norm(data_to_cube * screen_up)),
    )
    if not np.isfinite(projected_unit) or projected_unit <= 1e-9:
        return 47.5
    return float(px_per_cube_unit * projected_unit)


def flat_visual_pixel_scale(style: dict) -> float:
    """Return the fixed pixel scale for flat atom and bond primitives.

    Scatter3d markers and lines are screen-space primitives. Flat figures
    therefore keep one calibrated scale across camera orientations and crops;
    only viewport framing is allowed to vary between panels.
    """
    try:
        value = float(style.get("flat_visual_pixel_scale", 30.0))
    except (TypeError, ValueError):
        return 30.0
    return value if np.isfinite(value) and value > 0 else 30.0


def _manual_aspect_scale(
    scene: dict, style: dict, topology_data: dict | None = None
) -> np.ndarray | None:
    """Return data-units per rendered cube unit for manual aspectratio.

    Plotly maps each data axis range into a rendered axis whose length is
    ``aspectratio[axis]``. A data-space vector must therefore be divided by
    ``half_range / aspectratio`` before projecting through the camera basis.
    """
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


def _camera_axis_projections(
    scene: dict,
    style: dict,
    *,
    viewport: ViewportSpec | None = None,
) -> list[list[float]] | None:
    """Reproject unit lattice-basis directions onto the camera screen plane."""
    camera = _plotly_camera_from_scene(scene, style)
    eye_raw = camera.get("eye") or {}
    up_raw = camera.get("up") or {}
    center_raw = camera.get("center") or {}

    def _coerce_xyz(raw, fallback):
        try:
            if isinstance(raw, dict):
                return np.array(
                    [
                        float(raw.get(k, fallback[i]))
                        for i, k in enumerate(("x", "y", "z"))
                    ],
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

    resolved_viewport = reuse_viewport(viewport, scene, style)
    cube_scale = np.asarray(resolved_viewport.cube_scale, dtype=float)
    M_cube = M[:3] / cube_scale[None, :]
    norms = np.linalg.norm(M_cube, axis=1)
    if not np.all(np.isfinite(norms)) or np.any(norms < 1e-12):
        return None
    basis_dirs = M_cube / norms[:, None]
    return [
        [float(np.dot(basis_dirs[i], right)), float(np.dot(basis_dirs[i], screen_up))]
        for i in range(3)
    ]


def _legacy_axis_cube_scale(scene: dict, style: dict) -> np.ndarray | None:
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
            xs = abs(float(override["x"][1]) - float(override["x"][0])) / 2.0
            ys = abs(float(override["y"][1]) - float(override["y"][0])) / 2.0
            zs = abs(float(override["z"][1]) - float(override["z"][0])) / 2.0
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


def _axis_cube_scale(
    scene: dict,
    style: dict,
    *,
    viewport: ViewportSpec | None = None,
) -> np.ndarray | None:
    """Return the cube scale from the shared viewport resolver.

    Keep this helper as a compatibility facade for compass and legacy callers;
    new rendering code should retain the full :class:`ViewportSpec` returned
    by :func:`resolve_viewport`.
    """
    resolved_viewport = reuse_viewport(viewport, scene, style)
    return np.asarray(resolved_viewport.cube_scale, dtype=float)


def _visible_atoms(scene: dict, style: dict):
    # Compass-only callers can provide lattice/camera metadata without atoms.
    atoms = scene.get("draw_atoms") or []
    if style.get("show_minor_only", False):
        atoms = [atom for atom in atoms if atom["is_minor"]]
    return atoms or scene.get("draw_atoms") or []


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
            radii = _display_atom_radii(atoms, style)
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
        radii = _display_atom_radii(atoms, style)
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
    geometry_points = _geometry_entity_points(scene)
    if geometry_points:
        geometry_arr = np.asarray(geometry_points, dtype=float)
        if overlay_bounds is None or _overlay_near_cell(geometry_arr):
            extras.extend(geometry_arr)

    if extras:
        extras_arr = np.array(extras, dtype=float)
        extras_min = extras_arr.min(axis=0)
        extras_max = extras_arr.max(axis=0)
        if atom_mins is None:
            atom_mins, atom_maxs = extras_min, extras_max
        else:
            atom_mins = np.minimum(atom_mins, extras_min)
            atom_maxs = np.maximum(atom_maxs, extras_max)

    # Expand viewport to include isosurface mesh extents when present.
    # Without this, low-isovalue surfaces that extend beyond the atom
    # positions get clipped at the Plotly axis boundary.
    cube_data = scene.get("cube_data")
    if cube_data is not None and style.get("isosurface_enabled", True):
        from .traces_isosurface import isosurface_mesh_extents

        iso_min, iso_max = isosurface_mesh_extents(scene, style)
        if iso_min is not None:
            if atom_mins is None:
                atom_mins, atom_maxs = iso_min, iso_max
            else:
                atom_mins = np.minimum(atom_mins, iso_min)
                atom_maxs = np.maximum(atom_maxs, iso_max)

    vector_overlays = scene.get("vector_overlays") or []
    if vector_overlays:
        from .overlay.vectors import vector_overlay_bounds

        vector_min, vector_max = vector_overlay_bounds(
            vector_overlays,
            lattice=scene.get("M"),
        )
        if vector_min is not None:
            if atom_mins is None:
                atom_mins, atom_maxs = vector_min, vector_max
            else:
                atom_mins = np.minimum(atom_mins, vector_min)
                atom_maxs = np.maximum(atom_maxs, vector_max)

    if atom_mins is None:
        return [[-1.0, 1.0], [-1.0, 1.0], [-1.0, 1.0]]

    span = np.maximum(atom_maxs - atom_mins, 0.8)
    pad = np.maximum(span * 0.06, 0.25)
    mins = atom_mins - pad
    maxs = atom_maxs + pad
    ranges = [
        [float(mins[0]), float(maxs[0])],
        [float(mins[1]), float(maxs[1])],
        [float(mins[2]), float(maxs[2])],
    ]
    return _auto_camera_fit_ranges(scene, style, ranges, extra_points=extras)


def _auto_camera_fit_ranges(
    scene: dict,
    style: dict,
    ranges: list[list[float]],
    *,
    extra_points: Iterable[Iterable[float]] | None = None,
) -> list[list[float]]:
    """Add a small orientation-aware margin to a default perspective view.

    ``_scene_ranges`` owns the data bounding box, while Plotly maps that box
    into a unit cube before applying the camera basis.  A diagonal camera can
    therefore project a corner of an otherwise perfectly valid axis-aligned
    box past the edge of the viewport.  This is especially visible for
    uploaded co-crystals whose default view is diagonal: the left/bottom
    molecule is clipped even though all of its Cartesian coordinates are in
    the axis ranges.

    Expand only legacy (position/focal-point) cameras.  Those cameras are the
    scene's initial camera; an interactive camera is stored in Plotly's
    ``eye`` form and must retain the user's exact viewport.  The expansion is
    computed from the projected eight corners and keeps the existing bounding
    box centre, so multi-molecule formula units remain centred on their
    displayed bounding box.  Orthographic views do not need this correction.
    """
    if not isinstance(style, dict):
        return ranges
    explicit = style.get("camera")
    if not isinstance(explicit, dict):
        explicit = scene.get("camera")
    if not isinstance(explicit, dict) or not {
        "position",
        "focal_point",
    }.issubset(explicit):
        return ranges

    projection = explicit.get("projection")
    if isinstance(projection, dict):
        projection = projection.get("type")
    if str(projection or style.get("projection", "perspective")).lower() in {
        "orthographic",
        "ortho",
    }:
        return ranges

    try:
        spans = np.asarray(
            [float(axis_range[1]) - float(axis_range[0]) for axis_range in ranges],
            dtype=float,
        )
        if spans.shape != (3,) or not np.all(np.isfinite(spans)) or np.any(spans <= 1e-9):
            return ranges
        center = np.asarray(
            [0.5 * (float(axis_range[0]) + float(axis_range[1])) for axis_range in ranges],
            dtype=float,
        )
        from ..compass import camera_screen_basis

        camera = _plotly_camera_from_scene(scene, style)
        right, screen_up = camera_screen_basis(camera)
        aspect = spans / float(np.max(spans))
        # Fit the displayed geometry rather than all eight corners of the
        # axis-aligned cube.  Opposite corners of a long oblique cell are
        # mathematically valid range points but are not necessarily drawn;
        # using them would zoom every Z'=2 structure out unnecessarily.
        points: list[np.ndarray] = []
        atoms = _visible_atoms(scene, style)
        if atoms:
            atom_scale = float(style.get("atom_scale", 1.0))
            for atom in atoms:
                try:
                    cart = np.asarray(atom.get("cart"), dtype=float)
                    radius = max(float(atom.get("atom_radius", 0.18)), 0.05) * atom_scale
                except (TypeError, ValueError):
                    continue
                if cart.shape != (3,) or not np.all(np.isfinite(cart)):
                    continue
                points.extend(
                    cart + radius * np.asarray(signs, dtype=float)
                    for signs in (
                        (-1, -1, -1),
                        (-1, -1, 1),
                        (-1, 1, -1),
                        (-1, 1, 1),
                        (1, -1, -1),
                        (1, -1, 1),
                        (1, 1, -1),
                        (1, 1, 1),
                    )
                )
        mode = style.get("display_mode", scene.get("display_mode"))
        if mode == "unit_cell" or bool(style.get("show_unit_cell", False)):
            matrix = scene.get("M")
            if matrix is not None:
                matrix = np.asarray(matrix, dtype=float)
                if matrix.shape == (3, 3) and np.all(np.isfinite(matrix)):
                    a, b, c = matrix
                    points.extend(
                        np.asarray(point, dtype=float)
                        for point in (
                            np.zeros(3),
                            a,
                            b,
                            c,
                            a + b,
                            a + c,
                            b + c,
                            a + b + c,
                        )
                    )
        points.extend(_geometry_entity_points(scene))
        if extra_points:
            for point in extra_points:
                try:
                    candidate = np.asarray(point, dtype=float)
                except (TypeError, ValueError):
                    continue
                if candidate.shape == (3,) and np.all(np.isfinite(candidate)):
                    points.append(candidate)
        if not points:
            return ranges
        displayed = np.asarray(points, dtype=float)
        if displayed.ndim != 2 or displayed.shape[1] != 3:
            return ranges
        normalized = (displayed - center[None, :]) / (0.5 * spans)[None, :]
        normalized *= aspect[None, :]
        projected = np.column_stack((normalized @ right, normalized @ screen_up))
        extent = float(np.max(np.abs(projected)))
        if not np.isfinite(extent) or extent <= 0.9:
            return ranges
        # Keep roughly 8% breathing room.  Never cap this safety expansion:
        # an oblique view of a layered hydroxide (for example Brucite) can
        # project a valid corner well beyond the axis-aligned box, and a hard
        # cap turns that corner into a clipped atom on the initial frame.
        scale = max(extent / 0.92, 1.0)
        if scale <= 1.0 + 1e-12:
            return ranges
        half = 0.5 * spans * scale
        fitted = np.column_stack((center - half, center + half))
        return [[float(row[0]), float(row[1])] for row in fitted]
    except (TypeError, ValueError, KeyError, IndexError, FloatingPointError):
        # Camera metadata is user-editable through the REST API.  A malformed
        # camera must not make an otherwise renderable scene disappear.
        return ranges


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


def figure_axis_layout(
    scene: dict,
    style: dict,
    xr=None,
    yr=None,
    zr=None,
    *,
    viewport: ViewportSpec | None = None,
) -> dict:
    """Preserve Cartesian scale using the final, possibly padded, axis ranges.

    Plotly's ``data`` aspect follows trace extents, not these explicit ranges.
    Unequal padding can therefore flatten a planar mesh even when its own
    vertices are isotropic. Range-derived manual aspect applies in every mode;
    neither coordinates nor caller-supplied viewport endpoints are changed.
    """
    if viewport is not None:
        xr, yr, zr = viewport.ranges
        aspect_kwargs = {"aspectmode": viewport.aspectmode}
        if viewport.aspectratio is not None:
            aspect_kwargs["aspectratio"] = dict(viewport.aspectratio)
    else:
        if xr is None or yr is None or zr is None:
            raise TypeError("figure_axis_layout requires ranges or a viewport")
        aspect = _range_aspect_ratio(xr, yr, zr)
        if aspect is not None:
            aspect_kwargs = {"aspectmode": "manual", "aspectratio": aspect}
        else:
            aspect_kwargs = {"aspectmode": "data"}

    # ``visible: false`` is what gl-plot3d honors (it skips ticks, axis
    # lines, grid and the zero line). The extra flags cover a layout that
    # is inspected before that merge, so a single tick cannot survive at
    # the bottom of the canvas.
    hidden_axis = {
        "visible": False,
        "showgrid": False,
        "showbackground": False,
        "showspikes": False,
        "showticklabels": False,
        "ticks": "",
        "zeroline": False,
        "zerolinewidth": 0,
        "title": {"text": ""},
    }
    return {
        "xaxis": {**hidden_axis, "range": xr},
        "yaxis": {**hidden_axis, "range": yr},
        "zaxis": {**hidden_axis, "range": zr},
        "camera": _plotly_camera_from_scene(scene, style),
        "uirevision": style.get("uirevision", str(scene.get("name", "scene"))),
        "bgcolor": style.get("background", "#FFFFFF"),
        **aspect_kwargs,
    }



from .viewport_uniform import uniform_camera, uniform_viewport  # noqa: F401
