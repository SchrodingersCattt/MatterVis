"""Plotly gl3d screen positions, including perspective and non-cubic aspect."""

from __future__ import annotations

import numpy as np

from mat_viewer.render.screen_projection import project_gl3d_pixels


RANGES = ((0.0, 10.0), (0.0, 10.0), (0.0, 10.0))
ASPECT = {"x": 1.0, "y": 1.0, "z": 1.0}
POINTS = np.asarray([[5.0, 5.0, 5.0], [1.0, 1.0, 1.0], [9.0, 2.0, 8.0]], dtype=float)


def _camera(distance, projection, center=(0.0, 0.0, 0.0)):
    direction = np.asarray([1.0, 1.0, 1.0], dtype=float)
    direction /= np.linalg.norm(direction)
    eye = direction * float(distance)
    return {
        "eye": {"x": float(eye[0]), "y": float(eye[1]), "z": float(eye[2])},
        "center": {"x": center[0], "y": center[1], "z": center[2]},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "projection": {"type": projection},
    }


def _legacy_extrema(points, camera, width, height):
    from mat_viewer.math.projection import camera_screen_basis

    right, up = camera_screen_basis(camera)
    center = points.mean(axis=0)
    projected = np.stack([(points - center) @ right, (points - center) @ up], axis=1)
    mins = projected.min(axis=0)
    span = np.maximum(projected.max(axis=0) - mins, 1e-9)
    px = (projected[:, 0] - mins[0]) / span[0] * width
    py = height - ((projected[:, 1] - mins[1]) / span[1] * height)
    return np.stack([px, py], axis=1)


def test_orthographic_eye_distance_does_not_change_pixels():
    near = project_gl3d_pixels(POINTS, camera=_camera(1.2, "orthographic"), ranges=RANGES, aspectratio=ASPECT, width=800, height=600)
    far = project_gl3d_pixels(POINTS, camera=_camera(4.0, "orthographic"), ranges=RANGES, aspectratio=ASPECT, width=800, height=600)
    assert np.allclose(near, far, atol=1e-6)


def test_perspective_zoom_pan_and_resize_move_pixels():
    base = project_gl3d_pixels(POINTS, camera=_camera(2.2, "perspective"), ranges=RANGES, aspectratio=ASPECT, width=800, height=600)
    zoomed = project_gl3d_pixels(POINTS, camera=_camera(0.7, "perspective"), ranges=RANGES, aspectratio=ASPECT, width=800, height=600)
    panned = project_gl3d_pixels(POINTS, camera=_camera(2.2, "perspective", center=(0.4, -0.2, 0.1)), ranges=RANGES, aspectratio=ASPECT, width=800, height=600)
    resized = project_gl3d_pixels(POINTS, camera=_camera(2.2, "perspective"), ranges=RANGES, aspectratio=ASPECT, width=400, height=700)
    assert not np.allclose(base, zoomed, atol=1.0)
    assert not np.allclose(base, panned, atol=1.0)
    assert not np.allclose(base[:, 0], resized[:, 0], atol=1.0)


def test_perspective_rectangle_disagrees_with_extrema_rescale():
    camera = _camera(0.55, "perspective", center=(0.25, -0.15, 0.05))
    pixels = project_gl3d_pixels(POINTS, camera=camera, ranges=RANGES, aspectratio={"x": 1.0, "y": 2.4, "z": 0.35}, width=900, height=500, projection="perspective")
    assert np.all(np.isfinite(pixels))
    target = int(np.argmin(np.linalg.norm(pixels - pixels.mean(axis=0), axis=1)))
    pad = 4.0
    x, y = pixels[target]
    inside = [
        index
        for index, (sx, sy) in enumerate(pixels)
        if abs(sx - x) <= pad and abs(sy - y) <= pad
    ]
    assert inside == [target]
    legacy = _legacy_extrema(POINTS, camera, 900, 500)
    legacy_inside = [
        index
        for index, (sx, sy) in enumerate(legacy)
        if x - pad <= sx <= x + pad and y - pad <= sy <= y + pad
    ]
    assert legacy_inside != [target]
