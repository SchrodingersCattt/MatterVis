"""Project Cartesian points through Plotly's gl3d camera.

``scene.camera`` is expressed in the normalized model box, not in data
units. Each layout axis is centered and scaled so its range covers
``[-aspect/2, aspect/2]``. Perspective uses a 45 degree vertical field and
clip planes 0.01 and 1000. Orthographic uses ``x = ±width/height`` and
``y = ±1`` in that same model space, so eye distance does not change the
pixel size. Screen y increases downward, matching the box-select overlay.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from .camera import CameraTransform
from .contracts import CameraSpec

PLOTLY_FOVY_DEG = 45.0
PLOTLY_NEAR = 0.01
PLOTLY_FAR = 1000.0
# A center inside the frustum is not a framed scene when the visible
# centers collapse to a clipped speck or a thin sliver. Fractions are of
# the shorter viewport side. A side-on pair whose long axis still spans
# SCENE_LINE_LONG_FRACTION stays framed.
SCENE_MIN_EXTENT_FRACTION = 0.12
SCENE_LINE_THIN_FRACTION = 0.02
SCENE_LINE_LONG_FRACTION = 0.20
# Fit keeps the target inside this fraction of the shorter viewport side.
# The same gap is the perspective near-face margin.
# Slightly inside the painted 8% margin so a one-pixel measurement
# still lands in that margin after the status bar and canvas inset.
FIT_MARGIN_FRACTION = 0.08
FIT_MAX_EYE_DISTANCE = 24.0
FIT_MIN_ASPECT_SCALE = 0.25
# A molecule can sit in a small part of its cell. The search has to be
# able to reach that cluster; 8 stops a water molecule inside an 8 Å cell
# at about half the margin.
FIT_MAX_ASPECT_SCALE = 48.0
FIT_SEARCH_STEPS = 24
# The perspective silhouette is the tangent cone, and that cone depends
# on the eye distance the search is still choosing. Two passes converge;
# the third is the check that the solved eye and the tangent agree.
PERSPECTIVE_TANGENT_PASSES = 3
# A drawn atom with no radius still needs an extent, or Fit treats it as a point.
FIT_ATOM_EXTENT_A = 0.4
DEFAULT_FIT_WIDTH = 939.0
DEFAULT_FIT_HEIGHT = 800.0


def _vec3(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return (
            float(value.get("x", default[0])),
            float(value.get("y", default[1])),
            float(value.get("z", default[2])),
        )
    coords = list(value)
    return (float(coords[0]), float(coords[1]), float(coords[2]))


def _projection_name(camera: Mapping[str, Any] | None, fallback: str) -> str:
    raw = (camera or {}).get("projection")
    if isinstance(raw, str) and raw:
        name = raw
    elif isinstance(raw, Mapping) and raw.get("type"):
        name = str(raw["type"])
    else:
        name = fallback
    return "orthographic" if name == "orthographic" else "perspective"


def _safe_up(
    eye: tuple[float, float, float],
    center: tuple[float, float, float],
    up: tuple[float, float, float],
) -> tuple[float, float, float]:
    forward = np.asarray(center, dtype=float) - np.asarray(eye, dtype=float)
    hinted = np.asarray(up, dtype=float)
    if float(np.linalg.norm(np.cross(forward, hinted))) >= 1e-8:
        return up
    for fallback in ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)):
        if float(np.linalg.norm(np.cross(forward, fallback))) >= 1e-8:
            return fallback
    return (0.0, 0.0, 1.0)


def model_coordinates(
    points: np.ndarray,
    ranges,
    aspectratio: Mapping[str, float] | None,
) -> np.ndarray:
    """Map data coordinates into Plotly's centered model box."""
    array = np.asarray(points, dtype=float)
    pairs = [(float(pair[0]), float(pair[1])) for pair in ranges]
    mids = np.asarray([0.5 * (lo + hi) for lo, hi in pairs], dtype=float)
    spans = np.asarray([hi - lo for lo, hi in pairs], dtype=float)
    if aspectratio is None:
        aspect = np.ones(3, dtype=float)
    else:
        aspect = np.asarray(
            [float(aspectratio[axis]) for axis in ("x", "y", "z")], dtype=float
        )
    return (array - mids) * aspect / np.maximum(spans, 1e-12)


def _gl3d_transform(
    camera: Mapping[str, Any],
    *,
    projection: str | None,
    width: float,
    height: float,
) -> CameraTransform:
    width = max(1, int(round(float(width))))
    height = max(1, int(round(float(height))))
    eye = _vec3(camera.get("eye"), (1.25, 1.25, 1.25))
    center = _vec3(camera.get("center"), (0.0, 0.0, 0.0))
    up = _safe_up(eye, center, _vec3(camera.get("up"), (0.0, 0.0, 1.0)))
    if math.dist(eye, center) < 1e-8:
        raise ValueError("camera eye and center must differ")
    name = _projection_name(camera, projection or "perspective")
    if projection:
        name = _projection_name({"projection": projection}, name)
    spec = CameraSpec(
        position=eye,
        target=center,
        up=up,
        projection=name,
        fov_y_deg=PLOTLY_FOVY_DEG,
        near=PLOTLY_NEAR,
        far=PLOTLY_FAR,
        ortho_scale=1.0,
    )
    return CameraTransform(spec, width, height)


def project_gl3d_pixels(
    points,
    *,
    camera: Mapping[str, Any],
    ranges,
    aspectratio: Mapping[str, float] | None,
    width: float,
    height: float,
    projection: str | None = None,
) -> np.ndarray:
    """Return ``(N, 2)`` screen pixels for ``points`` under one gl3d camera."""
    array = np.asarray(points, dtype=float)
    if array.size == 0:
        return np.empty((0, 2), dtype=float)
    model = model_coordinates(array, ranges, aspectratio)
    return _gl3d_transform(
        camera, projection=projection, width=width, height=height
    ).project_world(model).xy


def camera_frames_points(
    camera: Mapping[str, Any] | None,
    points,
    *,
    ranges,
    aspectratio: Mapping[str, float] | None,
    projection: str | None = None,
    width: float = 1000,
    height: float = 800,
) -> bool:
    """Return whether any scene point lies inside the gl3d view frustum.

    Plotly's camera is in the normalized model box. A drag saved onto the
    wrong scene can look at a point outside that box, so the structure
    survives only as a clipped cap on the viewport edge. Atom centers are
    the points that have to remain on screen; a center outside the frustum
    is not a framed view.
    """
    array = np.asarray(points, dtype=float)
    if array.size == 0:
        return True
    if array.ndim != 2 or array.shape[1] != 3:
        return False
    finite = array[np.all(np.isfinite(array), axis=1)]
    if len(finite) == 0:
        return False
    if not isinstance(camera, Mapping):
        return False
    try:
        model = model_coordinates(finite, ranges, aspectratio)
        projected = _gl3d_transform(
            camera, projection=projection, width=width, height=height
        ).project_world(model)
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return bool(np.any(projected.visible))


def camera_shows_scene(
    camera: Mapping[str, Any] | None,
    points,
    *,
    ranges,
    aspectratio: Mapping[str, float] | None,
    projection: str | None = None,
    width: float = 1000,
    height: float = 800,
) -> bool:
    """Return whether the camera shows a scene, not only a clipped cap.

    ``camera_frames_points`` is true when any atom center lies inside the
    gl3d frustum. A drag or a stale scope camera can leave one center on
    the edge, or collapse the structure to a line a few pixels tall, and
    still pass that test. This check requires the visible centers to span
    ``SCENE_MIN_EXTENT_FRACTION`` of the short side, unless one axis is a
    side-on pair (thinner than ``SCENE_LINE_THIN_FRACTION``) whose other
    axis still reaches ``SCENE_LINE_LONG_FRACTION``. Fewer than two finite
    centers cannot span a rectangle; any framed center counts as shown.
    """
    array = np.asarray(points, dtype=float)
    if array.size == 0:
        return True
    if array.ndim != 2 or array.shape[1] != 3:
        return False
    finite = array[np.all(np.isfinite(array), axis=1)]
    if len(finite) == 0:
        return False
    if len(finite) < 2:
        return camera_frames_points(
            camera,
            finite,
            ranges=ranges,
            aspectratio=aspectratio,
            projection=projection,
            width=width,
            height=height,
        )
    if not isinstance(camera, Mapping):
        return False
    try:
        model = model_coordinates(finite, ranges, aspectratio)
        projected = _gl3d_transform(
            camera, projection=projection, width=width, height=height
        ).project_world(model)
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    if not np.any(projected.visible):
        return False
    visible_xy = projected.xy[projected.visible]
    span = visible_xy.max(axis=0) - visible_xy.min(axis=0)
    short_side = max(1.0, float(min(width, height)))
    fractions = [float(span[0]) / short_side, float(span[1]) / short_side]
    thin, long = sorted(fractions)
    if long < SCENE_MIN_EXTENT_FRACTION:
        return False
    if thin < SCENE_LINE_THIN_FRACTION and long < SCENE_LINE_LONG_FRACTION:
        return False
    return True


def _viewport_length(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number < 1.0:
        return default
    return number


def _aspect_vector(aspectratio: Mapping[str, float] | None) -> np.ndarray:
    if not aspectratio:
        return np.ones(3, dtype=float)
    return np.asarray([float(aspectratio[axis]) for axis in ("x", "y", "z")], dtype=float)


def _minimum_perspective_distance(
    direction: np.ndarray,
    aspectratio: Mapping[str, float] | None,
) -> float:
    """Closest eye distance that keeps the model box beyond the near plane."""

    front = 0.5 * float(np.dot(_aspect_vector(aspectratio), np.abs(direction)))
    return front + PLOTLY_NEAR + FIT_MARGIN_FRACTION


def _camera_at_distance(camera: Mapping[str, Any], distance: float) -> dict[str, Any]:
    eye = np.asarray(_vec3(camera.get("eye"), (1.25, 1.25, 1.25)), dtype=float)
    center = np.asarray(_vec3(camera.get("center"), (0.0, 0.0, 0.0)), dtype=float)
    direction = eye - center
    norm = float(np.linalg.norm(direction))
    if norm < 1e-8:
        direction = np.array([1.0, 1.0, 1.0], dtype=float)
        norm = float(np.linalg.norm(direction))
    updated = dict(camera)
    point = center + direction / norm * float(distance)
    updated["eye"] = {axis: float(point[index]) for index, axis in enumerate(("x", "y", "z"))}
    return updated


def _scaled_aspect(aspectratio: Mapping[str, float] | None, scale: float):
    if aspectratio is None:
        return None
    return {axis: float(aspectratio[axis]) * float(scale) for axis in ("x", "y", "z")}


def _pan_camera(camera: Mapping[str, Any], offset: np.ndarray) -> dict[str, Any]:
    """Move the eye and the center together so the view direction stays put."""

    eye = np.asarray(_vec3(camera.get("eye"), (1.25, 1.25, 1.25)), dtype=float)
    center = np.asarray(_vec3(camera.get("center"), (0.0, 0.0, 0.0)), dtype=float)
    updated = dict(camera)
    moved_eye = eye + offset
    moved_center = center + offset
    updated["eye"] = {axis: float(moved_eye[index]) for index, axis in enumerate("xyz")}
    updated["center"] = {
        axis: float(moved_center[index]) for index, axis in enumerate("xyz")
    }
    return updated


def _recenter_on_clear_viewport(
    camera: Mapping[str, Any],
    points: np.ndarray,
    *,
    ranges,
    aspectratio: Mapping[str, float] | None,
    projection: str,
    width: float,
    height: float,
    shown_height: float,
) -> dict[str, Any]:
    """Pan so the projected target box is centered in the clear viewport.

    NDC puts the model origin on the canvas center. The status bar and the
    Fit button cover the bottom of that canvas, so a box centered on the
    canvas sits low in the clear window. The shift is measured from the
    projected box, not from the origin, because an off-center cluster does
    not sit on the origin.
    """

    try:
        from ..compass import camera_screen_basis

        right, screen_up = camera_screen_basis(camera)
    except (TypeError, ValueError, KeyError):
        return dict(camera)
    right = np.asarray(right, dtype=float)
    screen_up = np.asarray(screen_up, dtype=float)
    current = dict(camera)
    probe = np.array(
        [[0.5 * (float(pair[0]) + float(pair[1])) for pair in ranges]],
        dtype=float,
    )
    for _ in range(4):
        try:
            xy = project_gl3d_pixels(
                points,
                camera=current,
                ranges=ranges,
                aspectratio=aspectratio,
                width=width,
                height=height,
                projection=projection,
            )
            base = project_gl3d_pixels(
                probe,
                camera=current,
                ranges=ranges,
                aspectratio=aspectratio,
                width=width,
                height=height,
                projection=projection,
            )[0]
            eps = 1e-3
            moved_x = project_gl3d_pixels(
                probe,
                camera=_pan_camera(current, eps * right),
                ranges=ranges,
                aspectratio=aspectratio,
                width=width,
                height=height,
                projection=projection,
            )[0]
            moved_y = project_gl3d_pixels(
                probe,
                camera=_pan_camera(current, eps * screen_up),
                ranges=ranges,
                aspectratio=aspectratio,
                width=width,
                height=height,
                projection=projection,
            )[0]
        except (TypeError, ValueError, ZeroDivisionError):
            return current
        center_x = 0.5 * (float(xy[:, 0].min()) + float(xy[:, 0].max()))
        center_y = 0.5 * (float(xy[:, 1].min()) + float(xy[:, 1].max()))
        delta = np.array(
            [0.5 * width - center_x, 0.5 * shown_height - center_y],
            dtype=float,
        )
        if float(np.max(np.abs(delta))) < 0.4:
            return current
        columns = np.column_stack(((moved_x - base) / eps, (moved_y - base) / eps))
        try:
            step = np.linalg.solve(columns, delta)
        except np.linalg.LinAlgError:
            return current
        current = _pan_camera(current, float(step[0]) * right + float(step[1]) * screen_up)
    return current


def _points_fit(
    points: np.ndarray,
    camera: Mapping[str, Any],
    *,
    ranges,
    aspectratio: Mapping[str, float] | None,
    projection: str,
    width: float,
    height: float,
    visible_height: float | None = None,
) -> bool:
    if len(points) == 0:
        return True
    try:
        model = model_coordinates(points, ranges, aspectratio)
        projected = _gl3d_transform(
            camera, projection=projection, width=width, height=height
        ).project_world(model)
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    if not bool(np.all(projected.visible)):
        return False
    shown_height = height if visible_height is None else min(height, visible_height)
    pad = FIT_MARGIN_FRACTION * min(width, shown_height)
    xy = projected.xy
    return bool(
        np.all(xy[:, 0] >= pad)
        and np.all(xy[:, 0] <= width - pad)
        and np.all(xy[:, 1] >= pad)
        and np.all(xy[:, 1] <= shown_height - pad)
    )


def _largest_that_fits(lo: float, hi: float, fits) -> float:
    if fits(hi):
        return hi
    if not fits(lo):
        return lo
    best = lo
    for _ in range(FIT_SEARCH_STEPS):
        mid = 0.5 * (lo + hi)
        if fits(mid):
            lo = mid
            best = mid
        else:
            hi = mid
    return best


def _smallest_that_fits(lo: float, hi: float, fits) -> float:
    if fits(lo):
        return lo
    if not fits(hi):
        return hi
    best = hi
    for _ in range(FIT_SEARCH_STEPS):
        mid = 0.5 * (lo + hi)
        if fits(mid):
            hi = mid
            best = mid
        else:
            lo = mid
    return best


def frame_gl3d_camera(
    camera: Mapping[str, Any],
    points,
    *,
    ranges,
    aspectratio: Mapping[str, float] | None,
    projection: str | None,
    width: Any = None,
    height: Any = None,
    visible_height: Any = None,
) -> tuple[dict[str, Any], float]:
    """Return a camera and uniform aspect scale that frame ``points``.

    Perspective moves the eye along its current direction. Orthographic
    pixel size does not follow eye length, so that projection scales the
    manual aspect ratio instead. Both then pan the eye and the center
    together until the target box is centered in the clear viewport.
    The clear viewport is shorter than the canvas when a control covers
    the bottom, so centering on the canvas leaves the top and bottom
    gaps unequal.
    """

    framed = dict(camera)
    name = _projection_name(framed, projection or "perspective")
    framed["projection"] = {"type": name}
    array = np.asarray(points, dtype=float)
    if array.size == 0:
        return framed, 1.0
    if array.ndim != 2 or array.shape[1] != 3:
        return framed, 1.0
    finite = array[np.all(np.isfinite(array), axis=1)]
    if len(finite) == 0:
        return framed, 1.0
    view_width = _viewport_length(width, DEFAULT_FIT_WIDTH)
    view_height = _viewport_length(height, DEFAULT_FIT_HEIGHT)
    shown_height = _viewport_length(visible_height, view_height)
    if shown_height > view_height:
        shown_height = view_height
    def placed_at(base: Mapping[str, Any], scale: float) -> tuple[dict[str, Any], bool]:
        scaled = _scaled_aspect(aspectratio, scale)
        placed = _recenter_on_clear_viewport(
            base,
            finite,
            ranges=ranges,
            aspectratio=scaled,
            projection=name,
            width=view_width,
            height=view_height,
            shown_height=shown_height,
        )
        if name == "perspective":
            eye = np.asarray(_vec3(placed.get("eye"), (1.25, 1.25, 1.25)), dtype=float)
            center = np.asarray(_vec3(placed.get("center"), (0.0, 0.0, 0.0)), dtype=float)
            direction = eye - center
            norm = float(np.linalg.norm(direction))
            if norm < 1e-8 or norm + 1e-6 < _minimum_perspective_distance(
                direction / max(norm, 1e-8), scaled
            ):
                return placed, False
        return placed, _points_fit(
            finite,
            placed,
            ranges=ranges,
            aspectratio=scaled,
            projection=name,
            width=view_width,
            height=view_height,
            visible_height=shown_height,
        )

    if name == "orthographic":
        def fits(scale: float) -> bool:
            return placed_at(framed, scale)[1]

        scale = _largest_that_fits(FIT_MIN_ASPECT_SCALE, FIT_MAX_ASPECT_SCALE, fits)
        return placed_at(framed, float(scale))[0], float(scale)

    eye = np.asarray(_vec3(framed.get("eye"), (1.25, 1.25, 1.25)), dtype=float)
    center = np.asarray(_vec3(framed.get("center"), (0.0, 0.0, 0.0)), dtype=float)
    direction = eye - center
    norm = float(np.linalg.norm(direction))
    if norm < 1e-8:
        direction = np.array([1.0, 1.0, 1.0], dtype=float)
        norm = float(np.linalg.norm(direction))
    direction = direction / norm

    def fits_distance(distance: float) -> bool:
        return placed_at(_camera_at_distance(framed, distance), 1.0)[1]

    distance = _smallest_that_fits(
        _minimum_perspective_distance(direction, aspectratio),
        FIT_MAX_EYE_DISTANCE,
        fits_distance,
    )
    placed = _camera_at_distance(framed, distance)

    def fits_scale(scale: float) -> bool:
        return placed_at(placed, scale)[1]

    # Eye distance cannot enlarge a cluster that is small inside the axis
    # box. The aspect scale is the remaining zoom, and it is 1 when the
    # eye already put the target on the margin.
    scale = _largest_that_fits(1.0, FIT_MAX_ASPECT_SCALE, fits_scale)
    return placed_at(placed, float(scale))[0], float(scale)


__all__ = [
    "DEFAULT_FIT_HEIGHT",
    "DEFAULT_FIT_WIDTH",
    "FIT_ATOM_EXTENT_A",
    "FIT_MARGIN_FRACTION",
    "PLOTLY_FOVY_DEG",
    "SCENE_LINE_LONG_FRACTION",
    "SCENE_LINE_THIN_FRACTION",
    "SCENE_MIN_EXTENT_FRACTION",
    "camera_frames_points",
    "camera_shows_scene",
    "frame_gl3d_camera",
    "model_coordinates",
    "project_gl3d_pixels",
]
