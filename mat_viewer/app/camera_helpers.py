from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *

class StaleCameraWrite(Exception):
    """A drag camera lost the compare-and-set at the locked write."""


# Sentinel for ``patch_state``: callers that omit the fence do not opt in.
_CAMERA_FENCE_UNSET = object()


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


def _camera_store_payload(scene_id: Optional[str], camera: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {"scene_id": scene_id, "camera": copy.deepcopy(camera)}


def _camera_figure_patch(
    scene: dict[str, Any],
    style: dict[str, Any],
    camera: Optional[dict[str, Any]],
    topology_data: Optional[dict[str, Any]] = None,
) -> Patch:
    patch = Patch()
    xr, yr, zr = _scene_ranges(
        scene,
        style,
        topology_data=topology_data if style.get("topology_enabled", False) else None,
    )
    scene_layout = figure_axis_layout(scene, style, xr, yr, zr)
    scene_layout["camera"] = copy.deepcopy(camera)
    for key, value in scene_layout.items():
        patch["layout"]["scene"][key] = value
    return patch


def _camera_from_store(camera_state: Optional[dict[str, Any]], scene_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not isinstance(camera_state, dict):
        return None
    if "camera" in camera_state:
        if camera_state.get("scene_id") != scene_id:
            return None
        camera = camera_state.get("camera")
        return copy.deepcopy(camera) if isinstance(camera, dict) else None
    # Backward-compatible with the old store shape, but only when the
    # selected scene id is unknown. Otherwise an old active-tab camera could
    # leak into the newly selected scene.
    if scene_id is None and "eye" in camera_state:
        return copy.deepcopy(camera_state)
    return None



def _status_message(message: str, level: str = "info") -> tuple[str, str]:
    return message, f"status-banner status-banner--{level}"


def _structure_summary(scene: dict) -> str:
    if not scene.get("draw_atoms"):
        return "No structure loaded yet. Upload a CIF to begin."
    disordered_atoms = sum(
        1 for atom in scene["draw_atoms"] if atom.get("is_disordered", False)
    )
    unresolved_atoms = sum(
        1
        for atom in scene["draw_atoms"]
        if atom.get("is_disordered", False)
        and not atom.get("disorder_resolved", False)
    )
    minor_atoms = sum(1 for atom in scene["draw_atoms"] if atom["is_minor"])
    minor_bonds = sum(1 for bond in scene["bonds"] if bond["is_minor"])
    overflow_count = len(scene.get("unwrap_overflow") or [])
    overflow_text = (
        f" {overflow_count} fragment(s) kept wrapped after exceeding the unwrap cap."
        if overflow_count
        else ""
    )
    if disordered_atoms:
        unresolved_text = (
            f" {unresolved_atoms} site(s) have no resolved major/minor "
            "assignment; occupancy is used visually."
            if unresolved_atoms
            else ""
        )
        return (
            f"Disorder detected: {disordered_atoms} site(s), "
            f"{minor_atoms} minor atoms, {minor_bonds} minor bonds."
            f"{unresolved_text}{overflow_text}"
        )
    return f"Disorder: none detected.{overflow_text}"


def _display_options_from_style(style: dict) -> list[str]:
    return [
        token
        for enabled, token in (
            (style.get("show_labels", True), "labels"),
            (style.get("show_axes", True), "axes"),
            (style.get("show_minor_only", False), "minor_only"),
            (style.get("minor_wireframe", False), "minor_wireframe"),
            (style.get("show_hydrogen", False), "hydrogens"),
            (style.get("show_unit_cell", False), "unit_cell_box"),
            (style.get("monochrome", False), "monochrome"),
        )
        if enabled
    ]


def _plotly_camera(camera: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not camera:
        return None
    if "eye" in camera:
        if "position" in camera or "focal_point" in camera:
            return {
                key: value
                for key, value in camera.items()
                if key not in ("position", "focal_point")
            }
        return camera
    position = np.array(camera.get("position", [0.0, 0.0, 1.0]), dtype=float)
    focal = np.array(camera.get("focal_point", [0.0, 0.0, 0.0]), dtype=float)
    up = np.array(camera.get("up", [0.0, 1.0, 0.0]), dtype=float)
    eye = position - focal
    norm = np.linalg.norm(eye)
    if norm < 1e-8:
        eye = np.array([0.0, 0.0, 1.8], dtype=float)
    else:
        eye = eye / norm * 1.8
    up_norm = np.linalg.norm(up)
    if up_norm < 1e-8:
        up = np.array([0.0, 1.0, 0.0], dtype=float)
    else:
        up = up / up_norm
    converted = {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
    }
    raw_projection = camera.get("projection")
    if isinstance(raw_projection, dict) and raw_projection.get("type"):
        converted["projection"] = {"type": str(raw_projection["type"])}
    elif isinstance(raw_projection, str) and raw_projection:
        converted["projection"] = {"type": raw_projection}
    return converted


def _relayout_touches_camera(relayout_data: Optional[dict[str, Any]]) -> bool:
    if not isinstance(relayout_data, dict):
        return False
    if isinstance(relayout_data.get("scene.camera"), dict):
        return True
    scene_payload = relayout_data.get("scene")
    if isinstance(scene_payload, dict) and isinstance(scene_payload.get("camera"), dict):
        return True
    return any(str(key).startswith("scene.camera.") for key in relayout_data)


def _persist_relayout_camera(
    backend,
    capture,
    camera_state,
    *,
    now: float,
    last_commit: float,
    min_interval_s: float,
):
    """Store a drag on the scene that produced it.

    ``capture["scene_id"]`` is stamped from the canvas when the gesture
    starts. The tab selected by the time Dash runs the callback can
    already be the next scene, and must not receive this camera.
    ``apply_intent`` persists ``set_camera`` with ``broadcast=False`` so
    the poll does not echo this camera back into the figure.
    """
    if not isinstance(capture, dict):
        return None
    scene_id = capture.get("scene_id")
    if not scene_id or scene_id not in backend.scene_store.scenes:
        return None
    relayout = capture.get("relayout") if isinstance(capture.get("relayout"), dict) else {}
    if not _relayout_touches_camera(relayout):
        return None
    full_camera_payload = bool(
        isinstance(relayout.get("scene.camera"), dict)
        or (
            isinstance(relayout.get("scene"), dict)
            and isinstance((relayout.get("scene") or {}).get("camera"), dict)
        )
    )
    state_before = backend.get_state(scene_id)
    # The browser stamps the scene generation and camera revision at gesture
    # start. A missing stamp is not an unrestricted write: a wheel timeout or
    # a scene handoff used to clear both fields and then store the old camera
    # on whatever canvas was mounted. Require both fences up front. The locked
    # write below compares them again so Reset/Fit can still win the race.
    captured_generation = capture.get("scene_generation")
    captured_camera_revision = capture.get("camera_revision")
    if captured_generation is None or captured_camera_revision is None:
        return None
    if str(captured_generation) != str(
        state_before.get("scene_generation", backend.scene_generation(scene_id))
    ):
        return None
    try:
        captured_camera_revision = int(captured_camera_revision)
    except (TypeError, ValueError):
        return None
    if captured_camera_revision != int(state_before.get("camera_revision", 0) or 0):
        return None
    # The revision fence above names the live server camera. The Dash store
    # may still contain the camera from before a projection command; using
    # it as the base of a partial relayout silently restores that projection.
    current_camera = state_before.get("camera") or _camera_from_store(camera_state, scene_id)
    camera = _camera_from_relayout_data(relayout, current_camera)
    if not camera or (isinstance(current_camera, dict) and camera == current_camera):
        return None
    if not full_camera_payload and (now - last_commit) < min_interval_s:
        return None
    replaced = False
    try:
        scene = backend.scene_for_state(state_before)
        style = backend.style_for_state(state_before, scene=scene)
        camera, replaced = backend.camera_that_frames(
            state_before, camera, scene=scene, style=style
        )
    except (KeyError, ValueError, OSError, TypeError):
        replaced = False
    payload: dict[str, Any] = {"camera": camera}
    # A refit is a new camera delivery even when the fitted default equals
    # the stored camera: the browser has moved away from it. Request the next
    # revision explicitly so that unchanged persisted values cannot reuse a
    # delivered key. Ordinary drags keep the clock; patch_state will not
    # increment this requested revision twice.
    if replaced:
        payload["camera_revision"] = int(state_before.get("camera_revision", 0) or 0) + 1
    committed = backend.apply_intent(
        {
            "type": "set_camera",
            "scene_id": scene_id,
            "payload": payload,
            "expected_scene_generation": captured_generation,
            "expected_camera_revision": captured_camera_revision,
        }
    )
    if committed.get("stale_camera"):
        return None
    state = backend.get_state(scene_id)
    flat_ortep = state.get("material") == "flat" and state.get("style") == "ortep"
    view_update = None
    if replaced:
        from .view_updates import make_update

        view_update = backend.broadcast_view_update(make_update(
            state,
            operation="camera",
            payload={"camera": copy.deepcopy(state.get("camera")), "command": "refit"},
            reason="camera-refit",
        ), state=state)
    return {
        "scene_id": str(scene_id),
        "camera_store": _camera_store_payload(scene_id, state.get("camera")),
        "agent_state": copy.deepcopy(state) if flat_ortep else None,
        "refit": replaced,
        "view_update": view_update,
    }


def _camera_from_relayout_data(
    relayout_data: Optional[dict[str, Any]],
    current_camera: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Extract a complete Plotly camera from Dash relayout payloads.

    Plotly may emit either ``{"scene.camera": {...}}`` or dotted partial
    updates such as ``{"scene.camera.eye.x": 1.2}``.  The latter used to be
    ignored, so the next checkbox-triggered redraw fell back to the default
    scene camera.
    """
    if not relayout_data:
        return None
    direct = relayout_data.get("scene.camera")
    if isinstance(direct, dict):
        return direct
    scene_payload = relayout_data.get("scene")
    if isinstance(scene_payload, dict) and isinstance(scene_payload.get("camera"), dict):
        return scene_payload["camera"]

    base = copy.deepcopy(_plotly_camera(current_camera) or {})
    changed = False

    def ensure_group(group: str) -> dict[str, float]:
        nonlocal changed
        value = base.setdefault(group, {})
        if not isinstance(value, dict):
            value = {}
            base[group] = value
        changed = True
        return value

    for group in ("eye", "center", "up"):
        group_payload = relayout_data.get(f"scene.camera.{group}")
        if isinstance(group_payload, dict):
            target = ensure_group(group)
            for axis in ("x", "y", "z"):
                if axis in group_payload:
                    target[axis] = float(group_payload[axis])
            continue
        for axis in ("x", "y", "z"):
            key = f"scene.camera.{group}.{axis}"
            if key in relayout_data:
                ensure_group(group)[axis] = float(relayout_data[key])
    projection = relayout_data.get("scene.camera.projection")
    if isinstance(projection, dict) and projection.get("type"):
        base["projection"] = copy.deepcopy(projection)
        changed = True
    projection_type = relayout_data.get("scene.camera.projection.type")
    if projection_type:
        base["projection"] = {"type": str(projection_type)}
        changed = True
    return base if changed else None


def _camera_vectors(camera: Optional[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cam = _plotly_camera(camera) or {
        "eye": {"x": 0.0, "y": 0.0, "z": 1.8},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 1.0, "z": 0.0},
    }
    eye = np.array([cam["eye"]["x"], cam["eye"]["y"], cam["eye"]["z"]], dtype=float)
    center = np.array([cam.get("center", {}).get("x", 0.0), cam.get("center", {}).get("y", 0.0), cam.get("center", {}).get("z", 0.0)], dtype=float)
    up = np.array([cam["up"]["x"], cam["up"]["y"], cam["up"]["z"]], dtype=float)
    up_norm = np.linalg.norm(up)
    if up_norm < 1e-8:
        up = np.array([0.0, 1.0, 0.0], dtype=float)
    else:
        up = up / up_norm
    return eye, center, up


def _camera_payload(
    eye: np.ndarray,
    center: np.ndarray,
    up: np.ndarray,
    *,
    projection: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
    }
    if projection is not None:
        payload["projection"] = {"type": str(projection)}
    return payload


# ---------------------------------------------------------------------
# Axis-aligned camera presets (VESTA-style "down a / b / c / a* / b* / c*")
# ---------------------------------------------------------------------
#
# ``M`` carries the lattice vectors as rows (M[0] = a, etc.).
# Fractional coordinates are row vectors (cart = frac @ M), so reciprocal
# vectors live in the columns of M^-1. ``camera_for_axis`` picks
# a unit view direction along the requested axis, picks an "up"
# reference axis from the remaining lattice vectors (real-space when
# the request is real-space, reciprocal-space when reciprocal), and
# uses Gram-Schmidt to orthogonalise that "up" against the view
# direction so non-orthogonal cells still produce a sane camera. The
# ``eye`` magnitude is preserved across alignments so the user's zoom
# level survives an axis switch.

_AXIS_VIEW_KEYS = ("a", "b", "c", "a*", "b*", "c*")


def _normalize_axis_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower().replace(" ", "")
    if text in {"a", "b", "c", "a*", "b*", "c*"}:
        return text
    # Tolerate alternative forms: "astar", "a-star", "areciprocal"
    aliases = {
        "astar": "a*", "a-star": "a*", "areciprocal": "a*", "a_reciprocal": "a*",
        "bstar": "b*", "b-star": "b*", "breciprocal": "b*", "b_reciprocal": "b*",
        "cstar": "c*", "c-star": "c*", "creciprocal": "c*", "c_reciprocal": "c*",
    }
    return aliases.get(text)


def _lattice_axes(M: np.ndarray) -> dict[str, np.ndarray]:
    """Return unit vectors for a, b, c, a*, b*, c* derived from
    cartesian row-lattice matrix ``M`` (rows = a, b, c)."""
    M_arr = np.asarray(M, dtype=float)
    if M_arr.shape != (3, 3):
        raise ValueError(f"expected 3x3 lattice matrix, got shape {M_arr.shape}")
    real_rows = [M_arr[i] for i in range(3)]
    try:
        recip = np.linalg.inv(M_arr)
    except np.linalg.LinAlgError as exc:
        raise ValueError("lattice matrix is singular; cannot build reciprocal axes") from exc
    recip_cols = [recip[:, i] for i in range(3)]
    out: dict[str, np.ndarray] = {}
    for key, vec in zip(("a", "b", "c"), real_rows):
        norm = float(np.linalg.norm(vec))
        out[key] = vec / norm if norm > 1e-12 else np.array([1.0, 0.0, 0.0])
    for key, vec in zip(("a*", "b*", "c*"), recip_cols):
        norm = float(np.linalg.norm(vec))
        out[key] = vec / norm if norm > 1e-12 else np.array([1.0, 0.0, 0.0])
    return out


def _orthogonalise_up(view_dir: np.ndarray, up_pick: np.ndarray) -> np.ndarray:
    """Project ``up_pick`` onto the plane perpendicular to ``view_dir``
    (Gram-Schmidt) and normalise. Falls back to a canonical up if the
    pick is degenerate (parallel to view_dir)."""
    proj = up_pick - float(np.dot(up_pick, view_dir)) * view_dir
    norm = float(np.linalg.norm(proj))
    if norm < 1e-9:
        # ``up_pick`` is parallel to ``view_dir`` -- pick the closest
        # canonical world axis that isn't.
        for fallback in (
            np.array([0.0, 0.0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
        ):
            proj = fallback - float(np.dot(fallback, view_dir)) * view_dir
            norm = float(np.linalg.norm(proj))
            if norm > 1e-9:
                break
    return proj / norm


def camera_for_axis(
    M: np.ndarray,
    axis: str,
    *,
    eye_distance: float = 1.8,
    center: Optional[np.ndarray] = None,
    projection: Optional[str] = None,
) -> dict[str, Any]:
    """Build a Plotly camera dict that looks down the requested axis.

    ``axis`` is one of ``a``, ``b``, ``c``, ``a*``, ``b*``, ``c*``.
    The camera ``up`` follows the VESTA convention:

    - looking down ``a``, ``b``  -> up = c (orthogonalised vs. view)
    - looking down ``c``         -> up = b
    - looking down ``a*``, ``b*`` -> up = c*
    - looking down ``c*``        -> up = b*

    Non-orthogonal cells go through Gram-Schmidt so the up is always
    perpendicular to the view direction; degenerate picks fall back
    to a canonical world axis.
    """
    key = _normalize_axis_key(axis)
    if key is None:
        raise ValueError(f"unknown axis: {axis!r}; pick one of {_AXIS_VIEW_KEYS}")
    axes = _lattice_axes(M)
    view_dir = axes[key]
    # VESTA-style up choice: pick the lattice axis that gives the
    # most useful "up" -- conventionally ``c`` for in-plane views,
    # ``b`` for the [001] view. Reciprocal lookups stay reciprocal.
    up_pick_map_real = {"a": "c", "b": "c", "c": "b"}
    up_pick_map_recip = {"a*": "c*", "b*": "c*", "c*": "b*"}
    up_key = up_pick_map_real.get(key) or up_pick_map_recip[key]
    up = _orthogonalise_up(view_dir, axes[up_key])
    center_arr = np.array([0.0, 0.0, 0.0]) if center is None else np.asarray(center, dtype=float)
    eye = center_arr + float(eye_distance) * view_dir
    return _camera_payload(eye, center_arr, up, projection=projection)


_VALID_PROJECTIONS = ("perspective", "orthographic")


def _coerce_projection(value: Any, *, fallback: str = "perspective") -> str:
    text = str(value or "").strip().lower()
    if text in _VALID_PROJECTIONS:
        return text
    return fallback


def _rotate_vector(vec: np.ndarray, axis: np.ndarray, angle_deg: float) -> np.ndarray:
    axis_norm = np.linalg.norm(axis)
    if axis_norm < 1e-8 or abs(angle_deg) < 1e-8:
        return vec
    axis = axis / axis_norm
    theta = np.deg2rad(angle_deg)
    return (
        vec * np.cos(theta)
        + np.cross(axis, vec) * np.sin(theta)
        + axis * np.dot(axis, vec) * (1.0 - np.cos(theta))
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


__all__ = [name for name in globals() if not name.startswith("__")]
