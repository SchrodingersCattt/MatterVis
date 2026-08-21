"""Deterministic polygon BSP used by the true-vector painter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from ..contracts import RGBA


@dataclass(frozen=True, slots=True)
class BSPPolygon:
    vertices: np.ndarray
    rgba: RGBA
    semantic_id: str
    source_order: int
    fragment_order: int = 0

    def __post_init__(self) -> None:
        vertices = np.array(self.vertices, dtype=float, copy=True)
        if vertices.ndim != 2 or vertices.shape[1:] != (3,) or len(vertices) < 3:
            raise ValueError("BSP polygons must have shape (N, 3), N >= 3")
        if not np.all(np.isfinite(vertices)):
            raise ValueError("BSP polygon vertices must be finite")
        vertices.setflags(write=False)
        object.__setattr__(self, "vertices", vertices)

    @property
    def normal(self) -> np.ndarray:
        return _polygon_plane(self.vertices)[0]


@dataclass(slots=True)
class BSPNode:
    normal: np.ndarray
    offset: float
    polygons: list[BSPPolygon]
    front: "BSPNode | None" = None
    back: "BSPNode | None" = None


def build_bsp(
    polygons: Iterable[BSPPolygon],
    *,
    epsilon: float = 1e-8,
) -> BSPNode | None:
    """Build a balanced deterministic BSP, splitting every spanning polygon."""
    items = [
        polygon for polygon in polygons if _polygon_area(polygon.vertices) > epsilon
    ]
    if not items:
        return None
    scene_scale = max(
        1.0,
        max(
            float(np.linalg.norm(vertex))
            for polygon in items
            for vertex in polygon.vertices
        ),
    )
    tolerance = max(float(epsilon), np.finfo(float).eps * scene_scale * 64.0)
    return _build(items, tolerance, depth=0)


def traverse_back_to_front(
    node: BSPNode | None,
    *,
    eye: np.ndarray | None = None,
    view_direction: np.ndarray | None = None,
) -> list[BSPPolygon]:
    """Return split polygons in painter order for one camera-space view.

    ``eye`` is the finite camera-space eye used by perspective projection.
    ``view_direction`` is the direction of parallel orthographic rays from
    the eye into the scene, equivalent to placing the eye at infinity in the
    opposite direction.  Omitting both retains the perspective-eye default at
    the camera-space origin.
    """
    if node is None:
        return []
    if eye is not None and view_direction is not None:
        raise ValueError("provide either eye or view_direction, not both")
    eye_point = np.zeros(3) if eye is None else np.asarray(eye, dtype=float)
    if eye_point.shape != (3,) or not np.all(np.isfinite(eye_point)):
        raise ValueError("eye must be a finite three-dimensional point")
    ray_direction = (
        None if view_direction is None else np.asarray(view_direction, dtype=float)
    )
    if ray_direction is not None:
        length = float(np.linalg.norm(ray_direction))
        if (
            ray_direction.shape != (3,)
            or not np.all(np.isfinite(ray_direction))
            or length <= 0.0
        ):
            raise ValueError(
                "view_direction must be a finite non-zero three-dimensional vector"
            )
        ray_direction = ray_direction / length
    result: list[BSPPolygon] = []

    def visit(current: BSPNode | None) -> None:
        if current is None:
            return
        if ray_direction is None:
            eye_side = float(eye_point @ current.normal - current.offset)
        else:
            # Eye = origin - distance * ray_direction.  At infinite distance,
            # the directional term selects the side; for a plane parallel to
            # the rays, the origin-offset term remains constant along the ray.
            eye_side = -float(ray_direction @ current.normal)
            if abs(eye_side) <= np.finfo(float).eps * 16.0:
                eye_side = -float(current.offset)
        far, near = (
            (current.back, current.front)
            if eye_side >= 0.0
            else (current.front, current.back)
        )
        visit(far)
        result.extend(sorted(current.polygons, key=_polygon_key))
        visit(near)

    visit(node)
    return result


def split_polygon(
    polygon: BSPPolygon,
    normal: np.ndarray,
    offset: float,
    *,
    epsilon: float,
) -> tuple[list[BSPPolygon], list[BSPPolygon], list[BSPPolygon]]:
    """Classify/split one polygon into front, back, and coplanar pieces."""
    distances = polygon.vertices @ normal - float(offset)
    positive = distances > epsilon
    negative = distances < -epsilon
    if np.any(positive) and np.any(negative):
        front_vertices, back_vertices = _split_vertices(
            polygon.vertices, distances, epsilon=epsilon
        )
        front = _fragment(polygon, front_vertices, 1)
        back = _fragment(polygon, back_vertices, 2)
        return ([front] if front else [], [back] if back else [], [])
    if np.any(positive):
        return [polygon], [], []
    if np.any(negative):
        return [], [polygon], []
    return [], [], [polygon]


def _build(polygons: list[BSPPolygon], epsilon: float, depth: int) -> BSPNode:
    if depth > 2048:
        raise RuntimeError(
            "BSP exceeded safe recursion depth; geometry is numerically degenerate"
        )
    splitter = _choose_splitter(polygons, epsilon)
    normal, offset = _polygon_plane(splitter.vertices)
    front: list[BSPPolygon] = []
    back: list[BSPPolygon] = []
    coplanar: list[BSPPolygon] = []
    for polygon in polygons:
        ahead, behind, same = split_polygon(polygon, normal, offset, epsilon=epsilon)
        front.extend(ahead)
        back.extend(behind)
        coplanar.extend(same)
    node = BSPNode(
        normal=normal,
        offset=float(offset),
        polygons=sorted(coplanar, key=_polygon_key),
    )
    if front:
        node.front = _build(front, epsilon, depth + 1)
    if back:
        node.back = _build(back, epsilon, depth + 1)
    return node


def _choose_splitter(polygons: list[BSPPolygon], epsilon: float) -> BSPPolygon:
    ordered = sorted(polygons, key=_polygon_key)
    if len(ordered) <= 1:
        return ordered[0]
    sample_indices = np.linspace(0, len(ordered) - 1, min(24, len(ordered)), dtype=int)
    best = ordered[int(sample_indices[0])]
    best_score: tuple[float, int, tuple] | None = None
    for sample_index in sample_indices:
        candidate = ordered[int(sample_index)]
        normal, offset = _polygon_plane(candidate.vertices)
        front = back = split = 0
        for polygon in ordered:
            distances = polygon.vertices @ normal - offset
            positive = bool(np.any(distances > epsilon))
            negative = bool(np.any(distances < -epsilon))
            if positive and negative:
                split += 1
                front += 1
                back += 1
            elif positive:
                front += 1
            elif negative:
                back += 1
        score = (split * 8.0 + abs(front - back), split, _polygon_key(candidate))
        if best_score is None or score < best_score:
            best, best_score = candidate, score
    return best


def _split_vertices(
    vertices: np.ndarray,
    distances: np.ndarray,
    *,
    epsilon: float,
) -> tuple[np.ndarray, np.ndarray]:
    front: list[np.ndarray] = []
    back: list[np.ndarray] = []
    for index, current in enumerate(vertices):
        following_index = (index + 1) % len(vertices)
        following = vertices[following_index]
        current_distance = float(distances[index])
        following_distance = float(distances[following_index])
        if current_distance >= -epsilon:
            front.append(current)
        if current_distance <= epsilon:
            back.append(current)
        if (current_distance > epsilon and following_distance < -epsilon) or (
            current_distance < -epsilon and following_distance > epsilon
        ):
            fraction = current_distance / (current_distance - following_distance)
            intersection = current + fraction * (following - current)
            front.append(intersection)
            back.append(intersection)
    return _deduplicate(front, epsilon), _deduplicate(back, epsilon)


def _deduplicate(vertices: list[np.ndarray], epsilon: float) -> np.ndarray:
    result: list[np.ndarray] = []
    for vertex in vertices:
        if not result or float(np.linalg.norm(vertex - result[-1])) > epsilon:
            result.append(np.asarray(vertex, dtype=float))
    if len(result) > 1 and float(np.linalg.norm(result[0] - result[-1])) <= epsilon:
        result.pop()
    return np.asarray(result, dtype=float).reshape(-1, 3)


def _fragment(
    source: BSPPolygon,
    vertices: np.ndarray,
    branch: int,
) -> BSPPolygon | None:
    if len(vertices) < 3 or _polygon_area(vertices) < 1e-14:
        return None
    return BSPPolygon(
        vertices=vertices,
        rgba=source.rgba,
        semantic_id=source.semantic_id,
        source_order=source.source_order,
        fragment_order=source.fragment_order * 3 + branch,
    )


def _polygon_plane(vertices: np.ndarray) -> tuple[np.ndarray, float]:
    origin = vertices[0]
    for index in range(1, len(vertices) - 1):
        normal = np.cross(vertices[index] - origin, vertices[index + 1] - origin)
        length = float(np.linalg.norm(normal))
        if length > 1e-14:
            normal /= length
            return normal, float(normal @ origin)
    raise ValueError("polygon is degenerate")


def _polygon_area(vertices: np.ndarray) -> float:
    if len(vertices) < 3:
        return 0.0
    normal_sum = np.zeros(3)
    origin = vertices[0]
    for index in range(1, len(vertices) - 1):
        normal_sum += np.cross(vertices[index] - origin, vertices[index + 1] - origin)
    return 0.5 * float(np.linalg.norm(normal_sum))


def _polygon_key(polygon: BSPPolygon) -> tuple:
    return (polygon.semantic_id, polygon.source_order, polygon.fragment_order)


__all__ = [
    "BSPNode",
    "BSPPolygon",
    "build_bsp",
    "split_polygon",
    "traverse_back_to_front",
]
