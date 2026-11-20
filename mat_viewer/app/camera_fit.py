"""Fit framing for a scene camera and its data box.

Layout conversion and the camera store stay in ``camera_helpers``.
The public Fit names are re-exported from there.
"""

from __future__ import annotations

from .shared import *

def apply_fitted_axis_layout(fig, scene: dict[str, Any], style: dict[str, Any]) -> None:
    """Write the current data-box ranges and aspect onto a cached figure.

    A cache hit otherwise keeps the ranges from the first build. That
    build is often the widened perspective box, and a later eye camera
    would paint the scale it solved on the tight box onto the wide one.
    """

    from ..render.viewport import figure_axis_layout
    from ..render.viewport_spec import resolve_viewport

    viewport = resolve_viewport(scene, style)
    axis_layout = figure_axis_layout(scene, style, viewport=viewport)
    fig.update_layout(
        scene_xaxis=axis_layout["xaxis"],
        scene_yaxis=axis_layout["yaxis"],
        scene_zaxis=axis_layout["zaxis"],
        scene_aspectmode=axis_layout.get("aspectmode"),
        scene_aspectratio=axis_layout.get("aspectratio"),
    )


def _cluster_axis_ranges(points: np.ndarray):
    """Padded axis box around the points Fit is actually framing.

    The pad is a fraction of the cluster itself. A fixed 0.8 Å floor
    leaves a single atom small inside the box, and aspect scale cannot
    enlarge it without driving the eye into that box.
    """

    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    span = np.maximum(maxs - mins, 1e-3)
    pad = np.maximum(span * 0.12, 0.02)
    return tuple(
        (float(mins[index] - pad[index]), float(maxs[index] + pad[index]))
        for index in range(3)
    )


def _screen_silhouette_offsets(camera, ranges, aspectratio) -> np.ndarray | None:
    """Data-space directions of one sphere's camera-plane silhouette.

    The camera basis is in the normalized model box. A data-space sphere
    becomes an ellipsoid there when the axis spans differ, so the support
    direction is ``S u / |S u|`` for the diagonal map ``S = aspect / span``.
    """

    try:
        from ..compass import camera_screen_basis

        right, screen_up = camera_screen_basis(camera)
    except (TypeError, ValueError, KeyError):
        return None
    spans = np.asarray([float(pair[1]) - float(pair[0]) for pair in ranges], dtype=float)
    if aspectratio is None:
        aspect = np.ones(3, dtype=float)
    else:
        try:
            aspect = np.asarray([float(aspectratio[axis]) for axis in ("x", "y", "z")], dtype=float)
        except (TypeError, ValueError, KeyError):
            return None
    if spans.shape != (3,) or np.any(spans <= 1e-12) or not np.all(np.isfinite(aspect)):
        return None
    metric = aspect / spans
    directions = []
    for raw in (right, screen_up):
        scaled = metric * np.asarray(raw, dtype=float)
        norm = float(np.linalg.norm(scaled))
        if not np.isfinite(norm) or norm < 1e-12:
            return None
        directions.append(scaled / norm)
        directions.append(-scaled / norm)
    return np.asarray(directions, dtype=float)


def _axis_shell(center: np.ndarray, radius: float) -> list[np.ndarray]:
    shell = []
    for axis in range(3):
        offset = np.zeros(3, dtype=float)
        offset[axis] = radius
        shell.append(center + offset)
        shell.append(center - offset)
    return shell


def _perspective_tangent_shell(
    center: np.ndarray,
    radius: float,
    camera: dict[str, Any],
    ranges,
    aspectratio: dict[str, float] | None,
) -> list[np.ndarray] | None:
    """Data points on the perspective tangent of one data-space sphere.

    The axis map turns that sphere into an ellipsoid. In the space where
    the ellipsoid is a unit sphere, the tangent circle is closer to the
    eye than the equator, so framing the equator clips a close sphere.
    """

    try:
        from ..compass import camera_screen_basis

        right, screen_up = camera_screen_basis(camera)
        eye = np.asarray(
            [float(camera["eye"][axis]) for axis in ("x", "y", "z")],
            dtype=float,
        )
    except (TypeError, ValueError, KeyError):
        return None
    pairs = [(float(pair[0]), float(pair[1])) for pair in ranges]
    mids = np.asarray([0.5 * (lo + hi) for lo, hi in pairs], dtype=float)
    spans = np.asarray([hi - lo for lo, hi in pairs], dtype=float)
    if aspectratio is None:
        aspect = np.ones(3, dtype=float)
    else:
        try:
            aspect = np.asarray([float(aspectratio[axis]) for axis in ("x", "y", "z")], dtype=float)
        except (TypeError, ValueError, KeyError):
            return None
    metric = aspect / np.maximum(spans, 1e-12)
    if not np.all(np.isfinite(metric)) or np.any(metric <= 1e-12):
        return None
    inverse = 1.0 / metric
    center_model = (np.asarray(center, dtype=float) - mids) * metric
    stretched = inverse * (eye - center_model) / radius
    distance = float(np.linalg.norm(stretched))
    if not np.isfinite(distance) or distance <= 1.001:
        return None
    axis = stretched / distance
    circle_center = stretched / (distance * distance)
    circle_radius = float(np.sqrt(max(0.0, 1.0 - 1.0 / (distance * distance))))
    shell = []
    for raw in (right, -right, screen_up, -screen_up):
        direction = inverse * np.asarray(raw, dtype=float) / radius
        direction = direction - float(np.dot(direction, axis)) * axis
        norm = float(np.linalg.norm(direction))
        if not np.isfinite(norm) or norm < 1e-8:
            return None
        stretched_point = circle_center + circle_radius * (direction / norm)
        shell.append(np.asarray(center, dtype=float) + radius * stretched_point)
    return shell


def _sphere_shell(
    center: np.ndarray,
    radius: float,
    camera: dict[str, Any] | None,
    ranges,
    aspectratio: dict[str, float] | None,
    projection: str,
) -> list[np.ndarray]:
    if radius <= 0.0:
        return []
    if camera is not None and ranges is not None and projection == "perspective":
        tangent = _perspective_tangent_shell(center, radius, camera, ranges, aspectratio)
        if tangent:
            return tangent
    if camera is not None and ranges is not None:
        silhouette = _screen_silhouette_offsets(camera, ranges, aspectratio)
        if silhouette is not None:
            return [center + radius * direction for direction in silhouette]
    return _axis_shell(center, radius)


def _append_sphere(points, center, radius, camera, ranges, aspectratio, projection) -> None:
    points.append(np.asarray(center, dtype=float))
    points.extend(_sphere_shell(center, radius, camera, ranges, aspectratio, projection))


def fit_target_points(
    scene: dict[str, Any],
    style: dict[str, Any],
    camera: dict[str, Any] | None = None,
    ranges=None,
    aspectratio: dict[str, float] | None = None,
    projection: str | None = None,
) -> np.ndarray:
    """Cell corners plus atom extents, or atom extents when the boundary is off."""

    from ..render.radius import atom_visual_radius
    from ..render.screen_projection import FIT_ATOM_EXTENT_A
    from ..render.traces_atoms import _coordination_ball_stick
    from ..render.traces_overlays import UNIT_CELL_TUBE_RADIUS_A

    mode = projection
    if mode is None and camera is not None:
        raw = camera.get("projection")
        if isinstance(raw, dict):
            raw = raw.get("type")
        mode = raw
    if mode is None:
        mode = style.get("projection")
    mode = str(mode or "perspective").lower()
    points: list[np.ndarray] = []
    if style.get("show_unit_cell"):
        from .backend_camera import _displayed_cell_corners

        for corner in _displayed_cell_corners(scene):
            _append_sphere(
                points,
                np.asarray(corner, dtype=float),
                UNIT_CELL_TUBE_RADIUS_A,
                camera,
                ranges,
                aspectratio,
                mode,
            )
    for atom in scene.get("draw_atoms") or []:
        cart = atom.get("cart")
        if cart is None:
            continue
        center = np.asarray(cart, dtype=float)
        raw_radius = atom.get("radius")
        if raw_radius is None:
            raw_radius = atom.get("atom_radius")
        try:
            radius = float(raw_radius or 0.0)
        except (TypeError, ValueError):
            radius = 0.0
        if not np.isfinite(radius) or radius <= 0.0:
            radius = FIT_ATOM_EXTENT_A
        try:
            # Mesh spheres are built with representation "ball" even when
            # the style is ball-and-stick. The 0.46 factor is not what is drawn.
            radius = atom_visual_radius(
                radius,
                element=str(atom.get("elem") or "C"),
                representation="ball",
                atom_scale=float(style.get("atom_scale") or 1.0),
                style=style,
                bond_radius=style.get("bond_radius"),
                coordination=_coordination_ball_stick(atom, style),
            )
        except (TypeError, ValueError):
            pass
        if ranges is not None:
            outside = False
            for coord, pair in zip(center, ranges):
                lo, hi = float(pair[0]), float(pair[1])
                if coord < lo or coord > hi:
                    outside = True
                    break
            if outside:
                continue
        _append_sphere(points, center, radius, camera, ranges, aspectratio, mode)
    if not points:
        return np.empty((0, 3), dtype=float)
    return np.asarray(points, dtype=float)


def framed_fit_camera(backend, state: dict[str, Any], payload: dict[str, Any] | None):
    """Frame the visible target for this scene and viewport size."""

    from ..render.screen_projection import PERSPECTIVE_TANGENT_PASSES, frame_gl3d_camera
    from ..render.viewport_spec import resolve_viewport

    clean = dict(state)
    clean["fit_axis_ranges"] = None
    scene = backend.scene_for_state(clean)
    style = dict(backend.style_for_state(state, scene=scene))
    style.pop("camera_aspect_scale", None)
    camera = backend.default_camera(state)
    # The structure camera is often still position/focal. That form makes
    # the viewport add the default perspective safety margin, so the
    # computed box is wider than the data. Fit frames the data box and
    # publishes it. An eye camera skips that margin.
    style["camera"] = {
        "eye": camera.get("eye"),
        "center": camera.get("center") or {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": camera.get("up"),
        "projection": camera.get("projection"),
    }
    viewport = resolve_viewport(scene, style)
    ranges = viewport.ranges
    aspect = viewport.aspectratio
    if not style.get("show_unit_cell"):
        bare = fit_target_points(scene, style)
        if len(bare):
            ranges = _cluster_axis_ranges(bare)
            spans = [hi - lo for lo, hi in ranges]
            longest = max(spans)
            aspect = {axis: spans[index] / longest for index, axis in enumerate("xyz")}
    projection = str(style.get("projection") or "perspective")
    passes = PERSPECTIVE_TANGENT_PASSES if projection.lower() == "perspective" else 1
    guess = style["camera"]
    fitted, scale = guess, 1.0
    for _ in range(passes):
        used_aspect = aspect
        if aspect is not None and scale != 1.0:
            used_aspect = {axis: float(aspect[axis]) * float(scale) for axis in ("x", "y", "z")}
        fitted, scale = frame_gl3d_camera(
            guess,
            fit_target_points(
                scene,
                style,
                camera=guess,
                ranges=ranges,
                aspectratio=used_aspect,
                projection=projection,
            ),
            ranges=ranges,
            aspectratio=aspect,
            projection=projection,
            width=(payload or {}).get("width"),
            height=(payload or {}).get("height"),
            visible_height=(payload or {}).get("visible_height"),
        )
        guess = fitted
    # The framed pose is the eye that was projected. A leftover
    # position/focal pair is a second camera and would expand the axis
    # range again on the next figure.
    fitted.pop("position", None)
    fitted.pop("focal_point", None)
    return fitted, scale, ranges


def fitted_camera_payload(state: dict[str, Any] | None) -> dict[str, Any]:
    """Camera delivery fields that keep a Fit on the layout.

    A later camera echo (a relayout persisted without the scale) used to
    send the eye alone. The scene plot then put the previous ratio back.
    The fitted ratio and the fitted data box travel with every camera
    delivery until Reset clears them.
    """

    payload: dict[str, Any] = {}
    if not isinstance(state, dict):
        return payload
    camera = state.get("camera")
    if isinstance(camera, dict):
        payload["camera"] = copy.deepcopy(camera)
    try:
        scale = float(state.get("camera_aspect_scale") or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    if scale == scale and scale > 0.0:
        payload["camera_aspect_scale"] = scale
    fitted = state.get("fit_axis_ranges")
    if isinstance(fitted, dict):
        ranges = {}
        for axis in ("x", "y", "z"):
            pair = fitted.get(axis)
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                ranges[axis] = [float(pair[0]), float(pair[1])]
        if len(ranges) == 3:
            payload["ranges"] = ranges
    return payload


def accepted_aspect_scale(value: Any) -> float | None:
    """Finite positive Fit scale. Malformed input does not become 1."""

    try:
        scale = float(value)
    except (TypeError, ValueError):
        return None
    if scale != scale or scale <= 0.0 or scale == float("inf"):
        return None
    return scale


def accepted_fit_axis_ranges(value: Any) -> dict[str, list[float]] | None | bool:
    """Parse a fitted data box.

    ``None`` clears the box. A dict of three finite intervals is stored.
    Any other shape, including a short pair, returns ``False`` so the
    previous box stays and ``patch_state`` does not raise.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        return False
    parsed: dict[str, list[float]] = {}
    for axis in ("x", "y", "z"):
        pair = value.get(axis)
        if isinstance(pair, (str, bytes)) or not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return False
        try:
            lo = float(pair[0])
            hi = float(pair[1])
        except (TypeError, ValueError):
            return False
        if lo != lo or hi != hi or lo == float("inf") or hi == float("inf") or not hi > lo:
            return False
        parsed[axis] = [lo, hi]
    return parsed


def confirmed_camera_fields(backend, state: dict[str, Any]) -> dict[str, Any]:
    """Fields a confirmed-state camera envelope must carry.

    The envelope is a second delivery of a camera the Fit already
    published. Without the fitted ratio, that delivery's scene plot
    stores the previous ratio.
    """

    fields: dict[str, Any] = {
        "camera": copy.deepcopy(state.get("camera")) if isinstance(state.get("camera"), dict) else state.get("camera"),
        "command": "state-confirmed",
    }
    aspect = framed_aspectratio(backend, state)
    if aspect is not None:
        fields["aspectratio"] = aspect
    ranges = fitted_camera_payload(state).get("ranges")
    if ranges:
        fields["ranges"] = ranges
    return fields


def publish_framed_camera(
    backend,
    scene_id: str | None,
    *,
    broadcast: bool,
    ranges=None,
    command: str | None = None,
) -> None:
    """Send the fitted camera and aspect even when the eye did not move.

    Orthographic Fit keeps the default eye. A camera-only classifier
    treats that as no camera change and drops the aspect scale.
    """

    if not broadcast:
        return
    from .view_updates import make_update

    state = backend.get_state(scene_id)
    if command == "reset" and ranges is None:
        from ..render.viewport_spec import resolve_viewport

        scene = backend.scene_for_state(state)
        style = backend.style_for_state(state, scene=scene)
        ranges = resolve_viewport(scene, style).ranges
    payload: dict[str, Any] = {"camera": state.get("camera")}
    aspect = framed_aspectratio(backend, state)
    if aspect is not None:
        payload["aspectratio"] = aspect
    # The default perspective figure widens the axis range so a diagonal
    # camera does not clip a corner. Fit frames the unexpanded data box,
    # so the live scene has to adopt that box in the same update or the
    # scale is applied to the wider range and the cell stays small.
    if ranges is not None:
        payload["ranges"] = {
            axis: [float(pair[0]), float(pair[1])]
            for axis, pair in zip(("x", "y", "z"), ranges)
        }
    if command:
        payload["command"] = command
    backend.broadcast_view_update(
        make_update(state, operation="camera", payload=payload),
        state=state,
    )


def framed_aspectratio(backend, state: dict[str, Any]):
    """Aspect ratio the layout and the live camera update both apply."""

    from ..render.viewport import scaled_scene_aspect
    from ..render.viewport_spec import resolve_viewport

    scene = backend.scene_for_state(state)
    style = backend.style_for_state(state, scene=scene)
    viewport = resolve_viewport(scene, style)
    return scaled_scene_aspect(viewport.aspectratio, style)


