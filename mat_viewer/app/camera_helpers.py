from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *

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
    minor_atoms = sum(1 for atom in scene["draw_atoms"] if atom["is_minor"])
    minor_bonds = sum(1 for bond in scene["bonds"] if bond["is_minor"])
    overflow_count = len(scene.get("unwrap_overflow") or [])
    overflow_text = (
        f" {overflow_count} fragment(s) kept wrapped after exceeding the unwrap cap."
        if overflow_count
        else ""
    )
    if minor_atoms:
        return f"Disorder detected: {minor_atoms} minor atoms, {minor_bonds} minor bonds.{overflow_text}"
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
    return {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": float(up[0]), "y": float(up[1]), "z": float(up[2])},
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


def _fallback_png(message: str) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return bytes.fromhex(
            "89504E470D0A1A0A0000000D4948445200000001000000010802000000907753DE"
            "0000000C49444154789C63606060000000040001F61738550000000049454E44AE426082"
        )
    image = Image.new("RGB", (960, 240), "white")
    draw = ImageDraw.Draw(image)
    draw.text((18, 18), message, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

__all__ = [name for name in globals() if not name.startswith("__")]
