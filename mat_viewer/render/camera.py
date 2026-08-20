"""Homogeneous camera transforms and deterministic clipping primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from .contracts import CameraSpec


_EPSILON = 1.0e-12


@dataclass(frozen=True, slots=True)
class ProjectedPoints:
    xy: np.ndarray
    depth: np.ndarray
    ndc: np.ndarray
    visible: np.ndarray


class CameraTransform:
    """Transform world coordinates into one camera and viewport.

    The implementation uses the conventional right-handed camera frame:
    camera looks along ``-z`` and visible depths are ``-camera_z > 0``.
    Projection methods return screen coordinates with y increasing downward,
    matching raster buffers and SVG coordinates.
    """

    def __init__(self, spec: CameraSpec, width: int, height: int):
        if int(width) <= 0 or int(height) <= 0:
            raise ValueError("viewport dimensions must be positive")
        self.spec = spec
        self.width = int(width)
        self.height = int(height)
        self.aspect = self.width / self.height
        self._view = _view_matrix(spec)
        self._projection = _projection_matrix(spec, self.aspect)
        self._view_projection = self._projection @ self._view
        self._inverse_view = np.linalg.inv(self._view)

    @property
    def view_matrix(self) -> np.ndarray:
        return self._view.copy()

    @property
    def projection_matrix(self) -> np.ndarray:
        return self._projection.copy()

    @property
    def view_projection_matrix(self) -> np.ndarray:
        return self._view_projection.copy()

    @property
    def right(self) -> np.ndarray:
        return self._view[0, :3].copy()

    @property
    def up(self) -> np.ndarray:
        return self._view[1, :3].copy()

    @property
    def forward(self) -> np.ndarray:
        return -self._view[2, :3].copy()

    def world_to_camera(
        self, points: np.ndarray | Iterable[Iterable[float]]
    ) -> np.ndarray:
        array = _points(points)
        homogeneous = np.column_stack((array, np.ones(len(array))))
        return (homogeneous @ self._view.T)[:, :3]

    def camera_to_world(
        self, points: np.ndarray | Iterable[Iterable[float]]
    ) -> np.ndarray:
        array = _points(points)
        homogeneous = np.column_stack((array, np.ones(len(array))))
        return (homogeneous @ self._inverse_view.T)[:, :3]

    def project_world(
        self, points: np.ndarray | Iterable[Iterable[float]]
    ) -> ProjectedPoints:
        return self.project_camera(self.world_to_camera(points))

    def project_camera(
        self, points: np.ndarray | Iterable[Iterable[float]]
    ) -> ProjectedPoints:
        camera = _points(points)
        homogeneous = np.column_stack((camera, np.ones(len(camera))))
        clip = homogeneous @ self._projection.T
        w = clip[:, 3]
        safe_w = np.where(np.abs(w) > _EPSILON, w, np.nan)
        ndc = clip[:, :3] / safe_w[:, None]
        xy = np.column_stack(
            (
                (ndc[:, 0] + 1.0) * 0.5 * self.width,
                (1.0 - ndc[:, 1]) * 0.5 * self.height,
            )
        )
        depth = -camera[:, 2]
        visible = (
            np.all(np.isfinite(ndc), axis=1)
            & (depth >= self.spec.near - _EPSILON)
            & (depth <= self.spec.far + _EPSILON)
            & (ndc[:, 0] >= -1.0 - _EPSILON)
            & (ndc[:, 0] <= 1.0 + _EPSILON)
            & (ndc[:, 1] >= -1.0 - _EPSILON)
            & (ndc[:, 1] <= 1.0 + _EPSILON)
        )
        return ProjectedPoints(xy=xy, depth=depth, ndc=ndc, visible=visible)

    def clip_polygon_world(self, points: np.ndarray) -> np.ndarray:
        """Clip a world polygon to near/far planes and return camera points."""
        return self.clip_polygon_camera(self.world_to_camera(points))

    def clip_polygon_camera(self, points: np.ndarray) -> np.ndarray:
        polygon = _points(points)
        if len(polygon) < 3:
            return np.empty((0, 3), dtype=float)
        polygon = _clip_polygon_depth(polygon, self.spec.near, keep_nearer=False)
        if len(polygon) >= 3:
            polygon = _clip_polygon_depth(polygon, self.spec.far, keep_nearer=True)
        return polygon

    def clip_segment_world(
        self, start: Iterable[float], end: Iterable[float]
    ) -> tuple[np.ndarray, np.ndarray] | None:
        camera = self.world_to_camera(np.asarray([start, end], dtype=float))
        return self.clip_segment_camera(camera[0], camera[1])

    def clip_segment_camera(
        self, start: Iterable[float], end: Iterable[float]
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Clip one segment to the depth interval using Liang-Barsky."""
        first = np.asarray(start, dtype=float)
        second = np.asarray(end, dtype=float)
        if first.shape != (3,) or second.shape != (3,):
            raise ValueError("segment endpoints must be three-dimensional")
        if not np.all(np.isfinite(first)) or not np.all(np.isfinite(second)):
            raise ValueError("segment endpoints must be finite")
        depth0, depth1 = -float(first[2]), -float(second[2])
        delta = depth1 - depth0
        low, high = 0.0, 1.0
        if abs(delta) < _EPSILON:
            if depth0 < self.spec.near or depth0 > self.spec.far:
                return None
        else:
            near_t = (self.spec.near - depth0) / delta
            far_t = (self.spec.far - depth0) / delta
            entry, exit_ = sorted((near_t, far_t))
            low = max(low, entry)
            high = min(high, exit_)
            if low > high + _EPSILON:
                return None
        vector = second - first
        return first + low * vector, first + high * vector


def triangulate_polygon(polygon: np.ndarray) -> list[np.ndarray]:
    """Triangulate a convex clipped polygon with stable fan ordering."""
    vertices = _points(polygon)
    if len(vertices) < 3:
        return []
    return [
        np.asarray([vertices[0], vertices[index], vertices[index + 1]])
        for index in range(1, len(vertices) - 1)
    ]


def _points(value: np.ndarray | Iterable[Iterable[float]]) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.ndim != 2 or array.shape[1:] != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(
            f"points must have shape (N, 3) and be finite; got {array.shape}"
        )
    return array


def _view_matrix(spec: CameraSpec) -> np.ndarray:
    position = np.asarray(spec.position, dtype=float)
    target = np.asarray(spec.target, dtype=float)
    up_hint = np.asarray(spec.up, dtype=float)
    forward = target - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, up_hint)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    up /= np.linalg.norm(up)
    view = np.eye(4, dtype=float)
    view[0, :3] = right
    view[1, :3] = up
    view[2, :3] = -forward
    view[0, 3] = -float(np.dot(right, position))
    view[1, 3] = -float(np.dot(up, position))
    view[2, 3] = float(np.dot(forward, position))
    return view


def _projection_matrix(spec: CameraSpec, aspect: float) -> np.ndarray:
    near, far = float(spec.near), float(spec.far)
    if spec.projection == "perspective":
        focal = 1.0 / np.tan(np.radians(float(spec.fov_y_deg)) * 0.5)
        matrix = np.zeros((4, 4), dtype=float)
        matrix[0, 0] = focal / aspect
        matrix[1, 1] = focal
        matrix[2, 2] = (far + near) / (near - far)
        matrix[2, 3] = 2.0 * far * near / (near - far)
        matrix[3, 2] = -1.0
        return matrix
    half_height = float(spec.ortho_scale)
    half_width = half_height * aspect
    matrix = np.eye(4, dtype=float)
    matrix[0, 0] = 1.0 / half_width
    matrix[1, 1] = 1.0 / half_height
    matrix[2, 2] = -2.0 / (far - near)
    matrix[2, 3] = -(far + near) / (far - near)
    return matrix


def _clip_polygon_depth(
    polygon: np.ndarray,
    boundary: float,
    *,
    keep_nearer: bool,
) -> np.ndarray:
    """Sutherland-Hodgman clip against one camera-depth plane."""
    if len(polygon) == 0:
        return polygon

    def inside(point: np.ndarray) -> bool:
        depth = -float(point[2])
        return (
            depth <= boundary + _EPSILON
            if keep_nearer
            else depth >= boundary - _EPSILON
        )

    result: list[np.ndarray] = []
    previous = polygon[-1]
    previous_inside = inside(previous)
    for current in polygon:
        current_inside = inside(current)
        if current_inside != previous_inside:
            depth_previous = -float(previous[2])
            depth_current = -float(current[2])
            denominator = depth_current - depth_previous
            fraction = (
                0.0
                if abs(denominator) < _EPSILON
                else (boundary - depth_previous) / denominator
            )
            result.append(previous + np.clip(fraction, 0.0, 1.0) * (current - previous))
        if current_inside:
            result.append(current)
        previous = current
        previous_inside = current_inside
    return np.asarray(result, dtype=float).reshape(-1, 3)


__all__ = ["CameraTransform", "ProjectedPoints", "triangulate_polygon"]
