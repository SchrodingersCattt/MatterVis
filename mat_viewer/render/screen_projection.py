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


__all__ = [
    "PLOTLY_FOVY_DEG",
    "SCENE_LINE_LONG_FRACTION",
    "SCENE_LINE_THIN_FRACTION",
    "SCENE_MIN_EXTENT_FRACTION",
    "camera_frames_points",
    "camera_shows_scene",
    "model_coordinates",
    "project_gl3d_pixels",
]
