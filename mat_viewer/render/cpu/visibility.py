"""Shared screen-space depth predicates for CPU renderers."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np


ProjectedSurface = tuple[np.ndarray, np.ndarray]
ProjectedStroke = tuple[np.ndarray, np.ndarray, float, tuple[float, ...]]


def anchor_occluded(
    surfaces: Iterable[ProjectedSurface],
    point: np.ndarray,
    depth: float,
    *,
    perspective: bool,
    strokes: Iterable[ProjectedStroke] = (),
    epsilon: float = 1.0e-8,
) -> bool:
    """Return whether opaque projected geometry is nearer than an anchor."""
    for xy, depths in surfaces:
        surface_depth = projected_polygon_depth(
            xy,
            depths,
            point,
            perspective=perspective,
        )
        if surface_depth is not None and surface_depth < float(depth) - float(epsilon):
            return True
    for xy, depths, width_px, dash in strokes:
        stroke_depth = projected_stroke_depth(
            xy,
            depths,
            point,
            width_px=width_px,
            dash=dash,
            perspective=perspective,
        )
        if stroke_depth is not None and stroke_depth < float(depth) - float(epsilon):
            return True
    return False


def projected_polygon_depth(
    xy: np.ndarray,
    depths: np.ndarray,
    point: np.ndarray,
    *,
    perspective: bool,
) -> float | None:
    """Interpolate polygon depth at one screen point using a convex fan."""
    for index in range(1, len(xy) - 1):
        triangle = np.asarray([xy[0], xy[index], xy[index + 1]])
        weights = _barycentric(point, triangle)
        if weights is None or np.any(weights < -1e-9):
            continue
        triangle_depths = np.asarray([depths[0], depths[index], depths[index + 1]])
        if perspective:
            reciprocal = float(np.sum(weights / triangle_depths))
            return 1.0 / max(reciprocal, 1e-15)
        return float(weights @ triangle_depths)
    return None


def projected_stroke_depth(
    xy: np.ndarray,
    depths: np.ndarray,
    point: np.ndarray,
    *,
    width_px: float,
    dash: tuple[float, ...],
    perspective: bool,
) -> float | None:
    """Return line depth when a screen point lies on its visible stroke."""
    vector = xy[1] - xy[0]
    length_squared = float(vector @ vector)
    if length_squared < 1e-16:
        return None
    parameter = float(np.clip((point - xy[0]) @ vector / length_squared, 0.0, 1.0))
    closest = xy[0] + parameter * vector
    if float(np.linalg.norm(point - closest)) > max(0.5, float(width_px) * 0.5):
        return None
    screen_length = float(np.sqrt(length_squared))
    if dash and not _dash_visible(parameter * screen_length, dash):
        return None
    if np.all(np.isneginf(depths)):
        return -np.inf
    if perspective:
        return 1.0 / (
            (1.0 - parameter) / float(depths[0]) + parameter / float(depths[1])
        )
    return float((1.0 - parameter) * depths[0] + parameter * depths[1])


def _barycentric(point: np.ndarray, triangle: np.ndarray) -> np.ndarray | None:
    first, second, third = triangle
    denominator = _cross2(second - first, third - first)
    if abs(denominator) < 1e-14:
        return None
    weight1 = _cross2(point - first, third - first) / denominator
    weight2 = _cross2(second - first, point - first) / denominator
    weight0 = 1.0 - weight1 - weight2
    return np.asarray([weight0, weight1, weight2])


def _cross2(first: np.ndarray, second: np.ndarray) -> float:
    return float(first[0] * second[1] - first[1] * second[0])


def _dash_visible(distance: float, pattern: tuple[float, ...]) -> bool:
    values = pattern if len(pattern) % 2 == 0 else pattern * 2
    period = float(sum(values))
    if period <= 0.0:
        return True
    position = float(distance) % period
    for index, length in enumerate(values):
        if position <= length:
            return index % 2 == 0
        position -= length
    return True


__all__ = [
    "ProjectedStroke",
    "ProjectedSurface",
    "anchor_occluded",
    "projected_polygon_depth",
    "projected_stroke_depth",
]
