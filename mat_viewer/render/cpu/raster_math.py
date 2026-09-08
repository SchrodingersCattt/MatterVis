from __future__ import annotations

import numpy as np

def _clip_polygon_attributes(
    points: np.ndarray,
    attributes: np.ndarray,
    *,
    near: float,
    far: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Clip camera points and linearly coupled vertex attributes together."""
    clipped_points = np.asarray(points, dtype=float)
    clipped_attributes = np.asarray(attributes, dtype=float)
    for boundary, keep_nearer in ((float(near), False), (float(far), True)):
        if len(clipped_points) == 0:
            break

        def inside(point: np.ndarray) -> bool:
            depth = -float(point[2])
            return (
                depth <= boundary + 1e-12 if keep_nearer else depth >= boundary - 1e-12
            )

        next_points: list[np.ndarray] = []
        next_attributes: list[np.ndarray] = []
        previous_point = clipped_points[-1]
        previous_attribute = clipped_attributes[-1]
        previous_inside = inside(previous_point)
        for current_point, current_attribute in zip(clipped_points, clipped_attributes):
            current_inside = inside(current_point)
            if current_inside != previous_inside:
                previous_depth = -float(previous_point[2])
                current_depth = -float(current_point[2])
                denominator = current_depth - previous_depth
                fraction = (
                    0.0
                    if abs(denominator) < 1e-15
                    else (boundary - previous_depth) / denominator
                )
                fraction = float(np.clip(fraction, 0.0, 1.0))
                next_points.append(
                    previous_point + fraction * (current_point - previous_point)
                )
                next_attributes.append(
                    previous_attribute
                    + fraction * (current_attribute - previous_attribute)
                )
            if current_inside:
                next_points.append(current_point)
                next_attributes.append(current_attribute)
            previous_point = current_point
            previous_attribute = current_attribute
            previous_inside = current_inside
        clipped_points = np.asarray(next_points, dtype=float).reshape(-1, 3)
        clipped_attributes = np.asarray(next_attributes, dtype=float).reshape(
            -1, attributes.shape[1]
        )
    return clipped_points, clipped_attributes


def _edge(first: np.ndarray, second: np.ndarray, point: np.ndarray) -> float:
    return float(
        (point[0] - first[0]) * (second[1] - first[1])
        - (point[1] - first[1]) * (second[0] - first[0])
    )


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


def _edge_array(
    first: np.ndarray, second: np.ndarray, points: np.ndarray
) -> np.ndarray:
    return (points[..., 0] - first[0]) * (second[1] - first[1]) - (
        points[..., 1] - first[1]
    ) * (second[0] - first[0])


__all__ = ["_clip_polygon_attributes", "_edge", "_dash_visible", "_edge_array"]
