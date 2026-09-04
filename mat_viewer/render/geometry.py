"""Pure NumPy geometry builders shared by every rendering backend."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Iterable, Sequence

import numpy as np

if TYPE_CHECKING:
    import plotly.graph_objects as go

from ..math.geometry import cylinder_vertices_faces, validate_mesh
from ..math.implicit import implicit_surface_mesh

from .contracts import LinePrimitive, RGBA, TriangleMeshPrimitive

_DEFAULT_COLOR = "#7C5CBF"

_BASIC_COLORS: dict[str, str] = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "gray": "#808080",
    "grey": "#808080",
    "yellow": "#ffff00",
    "orange": "#ffa500",
    "purple": "#800080",
}


def color_to_rgba(value: Any, *, alpha: float | None = None) -> RGBA:
    """Parse a compact CSS/array color without importing a graphics stack."""
    if isinstance(value, str):
        text = _BASIC_COLORS.get(value.strip().lower(), value.strip()).lstrip("#")
        if len(text) in (3, 4):
            text = "".join(character * 2 for character in text)
        if len(text) not in (6, 8):
            raise ValueError(f"unsupported color: {value!r}")
        try:
            channels = [
                int(text[index : index + 2], 16) / 255.0
                for index in range(0, len(text), 2)
            ]
        except ValueError as exc:
            raise ValueError(f"unsupported color: {value!r}") from exc
        if len(channels) == 3:
            channels.append(1.0)
    else:
        channels = np.asarray(value, dtype=float).reshape(-1).tolist()
        if len(channels) == 3:
            channels.append(1.0)
        if len(channels) != 4:
            raise ValueError("color must contain three or four channels")
    if alpha is not None:
        channels[3] = float(alpha)
    if any(
        not np.isfinite(channel) or channel < 0.0 or channel > 1.0
        for channel in channels
    ):
        raise ValueError("color channels must lie in [0, 1]")
    return tuple(float(channel) for channel in channels)  # type: ignore[return-value]


def sphere_mesh(
    center: Iterable[float],
    radius: float,
    *,
    lat_steps: int = 12,
    lon_steps: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return an indexed UV sphere without duplicate pole vertices."""
    center_array = _point(center, name="center")
    radius = _positive(radius, name="radius")
    lat_steps, lon_steps = int(lat_steps), int(lon_steps)
    if lat_steps < 2 or lon_steps < 3:
        raise ValueError("lat_steps must be >= 2 and lon_steps >= 3")

    unit_vertices = [[0.0, 0.0, 1.0]]
    for latitude in range(1, lat_steps):
        theta = math.pi * latitude / lat_steps
        for longitude in range(lon_steps):
            phi = 2.0 * math.pi * longitude / lon_steps
            unit_vertices.append(
                [
                    math.sin(theta) * math.cos(phi),
                    math.sin(theta) * math.sin(phi),
                    math.cos(theta),
                ]
            )
    south = len(unit_vertices)
    unit_vertices.append([0.0, 0.0, -1.0])

    triangles: list[list[int]] = []
    first_ring = 1
    for longitude in range(lon_steps):
        following = (longitude + 1) % lon_steps
        triangles.append([0, first_ring + longitude, first_ring + following])
    for latitude in range(lat_steps - 2):
        ring = 1 + latitude * lon_steps
        following_ring = ring + lon_steps
        for longitude in range(lon_steps):
            following = (longitude + 1) % lon_steps
            a, b = ring + longitude, ring + following
            c, d = following_ring + longitude, following_ring + following
            triangles.extend(([a, c, b], [b, c, d]))
    last_ring = 1 + (lat_steps - 2) * lon_steps
    for longitude in range(lon_steps):
        following = (longitude + 1) % lon_steps
        triangles.append([last_ring + longitude, south, last_ring + following])

    normals = np.asarray(unit_vertices, dtype=float)
    vertices = center_array[None, :] + radius * normals
    return vertices, np.asarray(triangles, dtype=np.int64), normals


def mesh_primitive(
    semantic_id: str,
    vertices: Any,
    triangles: Any,
    color: Any,
    *,
    normals: Any | None = None,
    alpha: float | None = None,
    double_sided: bool = True,
    metadata: dict[str, Any] | None = None,
) -> TriangleMeshPrimitive:
    """Wrap a precomputed indexed mesh without importing its generator.

    Cube adapters use this boundary after lazily running marching cubes; the
    core renderer consequently never imports scikit-image.
    """
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=np.asarray(vertices, dtype=float),
        triangles=np.asarray(triangles, dtype=np.int64),
        vertex_normals=(
            np.asarray(normals, dtype=float) if normals is not None else None
        ),
        rgba=color_to_rgba(color, alpha=alpha),
        double_sided=double_sided,
        metadata=metadata or {},
    )


def sphere_primitive(
    semantic_id: str,
    center: Iterable[float],
    radius: float,
    color: Any,
    *,
    lat_steps: int = 12,
    lon_steps: int = 20,
    alpha: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> TriangleMeshPrimitive:
    center_array = _point(center, name="center")
    radius_value = _positive(radius, name="radius")
    vertices, triangles, normals = sphere_mesh(
        center_array, radius_value, lat_steps=lat_steps, lon_steps=lon_steps
    )
    raster_metadata = dict(metadata or {})
    raster_metadata.update(
        {
            "_raster_shape": "sphere",
            "_raster_center": tuple(float(value) for value in center_array),
            "_raster_radius": radius_value,
        }
    )
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=vertices,
        triangles=triangles,
        vertex_normals=normals,
        rgba=color_to_rgba(color, alpha=alpha),
        metadata=raster_metadata,
    )


def cylinder_mesh(
    start: Iterable[float],
    end: Iterable[float],
    radius: float,
    *,
    sides: int = 12,
    capped: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return a closed cylinder with deterministic radial orientation."""
    first = _point(start, name="start")
    second = _point(end, name="end")
    radius = _positive(radius, name="radius")
    sides = int(sides)
    if sides < 3:
        raise ValueError("sides must be at least 3")
    axis = second - first
    length = float(np.linalg.norm(axis))
    if length < 1e-12:
        raise ValueError("cylinder endpoints must differ")
    direction = axis / length
    reference = np.array([0.0, 0.0, 1.0])
    if abs(float(direction @ reference)) > 0.92:
        reference = np.array([0.0, 1.0, 0.0])
    radial_u = np.cross(direction, reference)
    radial_u /= np.linalg.norm(radial_u)
    radial_v = np.cross(direction, radial_u)
    angles = np.linspace(0.0, 2.0 * math.pi, sides, endpoint=False)
    radial = (
        np.cos(angles)[:, None] * radial_u[None, :]
        + np.sin(angles)[:, None] * radial_v[None, :]
    )
    vertices = np.vstack((first + radius * radial, second + radius * radial))
    normals = np.vstack((radial, radial))
    triangles: list[list[int]] = []
    for index in range(sides):
        following = (index + 1) % sides
        triangles.extend(
            (
                [index, sides + index, following],
                [following, sides + index, sides + following],
            )
        )
    if capped:
        first_center = len(vertices)
        second_center = first_center + 1
        vertices = np.vstack((vertices, first, second))
        normals = np.vstack((normals, -direction, direction))
        for index in range(sides):
            following = (index + 1) % sides
            triangles.extend(
                (
                    [first_center, following, index],
                    [second_center, sides + index, sides + following],
                )
            )
    return vertices, np.asarray(triangles, dtype=np.int64), normals


def cylinder_primitive(
    semantic_id: str,
    start: Iterable[float],
    end: Iterable[float],
    radius: float,
    color: Any,
    *,
    sides: int = 12,
    alpha: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> TriangleMeshPrimitive:
    first = _point(start, name="start")
    second = _point(end, name="end")
    radius_value = _positive(radius, name="radius")
    vertices, triangles, normals = cylinder_mesh(
        first, second, radius_value, sides=sides
    )
    raster_metadata = dict(metadata or {})
    raster_metadata.update(
        {
            "_raster_shape": "cylinder",
            "_raster_start": tuple(float(value) for value in first),
            "_raster_end": tuple(float(value) for value in second),
            "_raster_radius": radius_value,
        }
    )
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=vertices,
        triangles=triangles,
        vertex_normals=normals,
        rgba=color_to_rgba(color, alpha=alpha),
        metadata=raster_metadata,
    )


def bond_primitives(
    semantic_id: str,
    start: Iterable[float],
    end: Iterable[float],
    radius: float,
    start_color: Any,
    end_color: Any | None = None,
    *,
    sides: int = 12,
    alpha: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> tuple[TriangleMeshPrimitive, ...]:
    """Build one or two endpoint-coloured cylinder halves for a bond."""
    first, second = _point(start, name="start"), _point(end, name="end")
    if float(np.linalg.norm(second - first)) < 1e-12:
        return ()
    if end_color is None or color_to_rgba(start_color) == color_to_rgba(end_color):
        return (
            cylinder_primitive(
                semantic_id,
                first,
                second,
                radius,
                start_color,
                sides=sides,
                alpha=alpha,
                metadata=metadata,
            ),
        )
    midpoint = 0.5 * (first + second)
    return (
        cylinder_primitive(
            f"{semantic_id}:a",
            first,
            midpoint,
            radius,
            start_color,
            sides=sides,
            alpha=alpha,
            metadata=metadata,
        ),
        cylinder_primitive(
            f"{semantic_id}:b",
            midpoint,
            second,
            radius,
            end_color,
            sides=sides,
            alpha=alpha,
            metadata=metadata,
        ),
    )


def bond_line_primitives(
    semantic_id: str,
    start: Iterable[float],
    end: Iterable[float],
    width_px: float,
    start_color: Any,
    end_color: Any | None = None,
    *,
    alpha: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> tuple[LinePrimitive, ...]:
    """Build one or two endpoint-coloured line halves for a bond."""

    first, second = _point(start, name="start"), _point(end, name="end")
    if float(np.linalg.norm(second - first)) < 1e-12:
        return ()
    if end_color is None or color_to_rgba(start_color) == color_to_rgba(end_color):
        return (
            LinePrimitive(
                semantic_id=semantic_id,
                segments=np.asarray([[first, second]]),
                rgba=color_to_rgba(start_color, alpha=alpha),
                width_px=width_px,
                metadata=metadata or {},
            ),
        )
    midpoint = 0.5 * (first + second)
    return (
        LinePrimitive(
            semantic_id=f"{semantic_id}:a",
            segments=np.asarray([[first, midpoint]]),
            rgba=color_to_rgba(start_color, alpha=alpha),
            width_px=width_px,
            metadata=metadata or {},
        ),
        LinePrimitive(
            semantic_id=f"{semantic_id}:b",
            segments=np.asarray([[midpoint, second]]),
            rgba=color_to_rgba(end_color, alpha=alpha),
            width_px=width_px,
            metadata=metadata or {},
        ),
    )


def ellipsoid_principal_axes(
    displacement: Any,
    *,
    probability: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    """Return probability-scaled semi-axis lengths and Cartesian axes."""
    matrix = np.asarray(displacement, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValueError("displacement must be a finite 3x3 matrix")
    matrix = 0.5 * (matrix + matrix.T)
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    if float(eigenvalues.min()) < -1e-10:
        raise ValueError("displacement matrix must be positive semidefinite")
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.clip(eigenvalues[order], 0.0, None)
    eigenvectors = eigenvectors[:, order]
    return _chi_square_radius(probability) * np.sqrt(eigenvalues), eigenvectors


def ellipsoid_mesh(
    center: Iterable[float],
    displacement: Any,
    *,
    probability: float = 0.5,
    lat_steps: int = 12,
    lon_steps: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lengths, axes = ellipsoid_principal_axes(displacement, probability=probability)
    unit_vertices, triangles, unit_normals = sphere_mesh(
        (0.0, 0.0, 0.0), 1.0, lat_steps=lat_steps, lon_steps=lon_steps
    )
    center_array = _point(center, name="center")
    transform = axes @ np.diag(lengths)
    vertices = center_array[None, :] + unit_vertices @ transform.T
    inverse_transpose = np.linalg.pinv(transform).T
    normals = unit_normals @ inverse_transpose.T
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    return vertices, triangles, normals


def ellipsoid_primitive(
    semantic_id: str,
    center: Iterable[float],
    displacement: Any,
    color: Any,
    *,
    probability: float = 0.5,
    lat_steps: int = 12,
    lon_steps: int = 20,
    alpha: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> TriangleMeshPrimitive:
    vertices, triangles, normals = ellipsoid_mesh(
        center,
        displacement,
        probability=probability,
        lat_steps=lat_steps,
        lon_steps=lon_steps,
    )
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=vertices,
        triangles=triangles,
        vertex_normals=normals,
        rgba=color_to_rgba(color, alpha=alpha),
        metadata=metadata or {},
    )


def ellipsoid_axes_primitive(
    semantic_id: str,
    center: Iterable[float],
    displacement: Any,
    color: Any = "#222222",
    *,
    probability: float = 0.5,
    width_px: float = 0.8,
) -> LinePrimitive:
    center_array = _point(center, name="center")
    lengths, axes = ellipsoid_principal_axes(displacement, probability=probability)
    segments = [
        [
            center_array - axes[:, index] * lengths[index],
            center_array + axes[:, index] * lengths[index],
        ]
        for index in range(3)
    ]
    return LinePrimitive(
        semantic_id=semantic_id,
        segments=np.asarray(segments),
        rgba=color_to_rgba(color),
        width_px=width_px,
    )


def ellipsoid_hatch_primitive(
    semantic_id: str,
    center: Iterable[float],
    displacement: Any,
    view_direction: Iterable[float],
    color: Any = "#202020",
    *,
    probability: float = 0.5,
    line_count: int = 5,
    arc_samples: int = 18,
    width_px: float = 0.7,
) -> LinePrimitive:
    """Build camera-facing ORTEP octant arcs on the ellipsoid surface."""
    center_array = _point(center, name="center")
    view = _point(view_direction, name="view_direction")
    view /= max(float(np.linalg.norm(view)), 1e-12)
    lengths, axes = ellipsoid_principal_axes(displacement, probability=probability)
    signs = max(
        (
            (sx, sy, sz)
            for sx in (-1.0, 1.0)
            for sy in (-1.0, 1.0)
            for sz in (-1.0, 1.0)
        ),
        key=lambda values: float(
            (axes @ (lengths * np.asarray(values, dtype=float))) @ view
        ),
    )
    sx, sy, sz = signs
    surface_lengths = lengths * 1.003
    polylines: list[np.ndarray] = []
    for line_index in range(1, max(1, int(line_count)) + 1):
        theta = 0.5 * math.pi * line_index / (int(line_count) + 1)
        phis = np.linspace(0.0, 0.5 * math.pi, max(3, int(arc_samples)))
        local = np.column_stack(
            (
                sx * math.sin(theta) * np.cos(phis),
                sy * math.sin(theta) * np.sin(phis),
                np.full_like(phis, sz * math.cos(theta)),
            )
        )
        polylines.append(center_array + (local * surface_lengths) @ axes.T)
    samples = max(3, int(arc_samples))
    thetas = np.linspace(0.0, 0.5 * math.pi, samples)
    for phi in (0.0, 0.5 * math.pi):
        local = np.column_stack(
            (
                sx * np.sin(thetas) * math.cos(phi),
                sy * np.sin(thetas) * math.sin(phi),
                sz * np.cos(thetas),
            )
        )
        polylines.append(center_array + (local * surface_lengths) @ axes.T)
    phis = np.linspace(0.0, 0.5 * math.pi, samples)
    equator = np.column_stack(
        (sx * np.cos(phis), sy * np.sin(phis), np.zeros_like(phis))
    )
    polylines.append(center_array + (equator * surface_lengths) @ axes.T)
    segments = np.concatenate(
        [np.stack((polyline[:-1], polyline[1:]), axis=1) for polyline in polylines],
        axis=0,
    )
    return LinePrimitive(
        semantic_id=semantic_id,
        segments=segments,
        rgba=color_to_rgba(color),
        width_px=width_px,
        metadata={"kind": "ortep_hatch"},
    )


def aromatic_ring_primitive(
    semantic_id: str,
    points: Any,
    color: Any,
    *,
    mode: str = "circle",
    normal: Any | None = None,
    alpha: float = 1.0,
    width_px: float = 1.5,
    samples: int = 64,
    radius_scale: float = 0.62,
    metadata: dict[str, Any] | None = None,
) -> LinePrimitive | TriangleMeshPrimitive:
    """Build a plane-fitted aromatic circle or disk from an ordered cycle."""
    ring = np.asarray(points, dtype=float)
    if (
        ring.ndim != 2
        or ring.shape[1:] != (3,)
        or len(ring) < 3
        or not np.all(np.isfinite(ring))
    ):
        raise ValueError("ring points must have shape (N, 3), N >= 3")
    center = ring.mean(axis=0)
    if normal is None:
        _u, _s, vh = np.linalg.svd(ring - center, full_matrices=False)
        normal_array = vh[-1]
    else:
        normal_array = _point(normal, name="normal")
    normal_array /= max(float(np.linalg.norm(normal_array)), 1e-12)
    radial = ring[0] - center
    radial -= float(radial @ normal_array) * normal_array
    if float(np.linalg.norm(radial)) < 1e-12:
        radial = ring[1] - center
        radial -= float(radial @ normal_array) * normal_array
    radial /= max(float(np.linalg.norm(radial)), 1e-12)
    tangent = np.cross(normal_array, radial)
    tangent /= max(float(np.linalg.norm(tangent)), 1e-12)
    projected_radii = np.linalg.norm(
        (ring - center) - ((ring - center) @ normal_array)[:, None] * normal_array,
        axis=1,
    )
    radius = max(float(np.mean(projected_radii)) * float(radius_scale), 1e-6)
    angles = np.linspace(0.0, 2.0 * math.pi, int(samples), endpoint=False)
    circle = center + radius * (
        np.cos(angles)[:, None] * radial + np.sin(angles)[:, None] * tangent
    )
    rgba = color_to_rgba(color, alpha=alpha)
    if mode == "circle":
        segments = np.stack((circle, np.roll(circle, -1, axis=0)), axis=1)
        return LinePrimitive(
            semantic_id=semantic_id,
            segments=segments,
            rgba=rgba,
            width_px=width_px,
            metadata=metadata or {},
        )
    if mode != "disk":
        raise ValueError("aromatic ring mode must be circle or disk")
    vertices = np.vstack((center, circle))
    triangles = np.asarray(
        [[0, 1 + index, 1 + (index + 1) % len(circle)] for index in range(len(circle))],
        dtype=np.int64,
    )
    normals = np.tile(normal_array, (len(vertices), 1))
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=vertices,
        triangles=triangles,
        vertex_normals=normals,
        rgba=rgba,
        metadata=metadata or {},
    )


def unit_cell_primitive(
    semantic_id: str,
    matrix: Any,
    color: Any = "#333333",
    *,
    origin: Iterable[float] = (0.0, 0.0, 0.0),
    width_px: float = 1.0,
    dash: Iterable[float] = (),
    alpha: float = 1.0,
    depth_test: bool = True,
    metadata: dict[str, Any] | None = None,
) -> LinePrimitive:
    lattice = np.asarray(matrix, dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        raise ValueError("matrix must be a finite 3x3 lattice with row vectors")
    origin_array = _point(origin, name="origin")
    fractions = np.asarray(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 1, 0],
            [1, 0, 1],
            [0, 1, 1],
            [1, 1, 1],
        ],
        dtype=float,
    )
    vertices = origin_array + fractions @ lattice
    edges = (
        (0, 1),
        (0, 2),
        (0, 3),
        (1, 4),
        (1, 5),
        (2, 4),
        (2, 6),
        (3, 5),
        (3, 6),
        (4, 7),
        (5, 7),
        (6, 7),
    )
    return LinePrimitive(
        semantic_id=semantic_id,
        segments=np.asarray(
            [[vertices[first], vertices[second]] for first, second in edges]
        ),
        rgba=color_to_rgba(color, alpha=alpha),
        width_px=width_px,
        dash=tuple(float(value) for value in dash),
        depth_test=depth_test,
        metadata=metadata or {"kind": "unit_cell"},
    )


def polyhedron_primitive(
    semantic_id: str,
    vertices: Any,
    faces: Sequence[Sequence[int]],
    color: Any,
    *,
    alpha: float = 0.50,
    metadata: dict[str, Any] | None = None,
) -> TriangleMeshPrimitive:
    points = np.asarray(vertices, dtype=float)
    if points.ndim != 2 or points.shape[1:] != (3,) or not np.all(np.isfinite(points)):
        raise ValueError("polyhedron vertices must have shape (N, 3)")
    triangles = triangulate_faces(faces, len(points))
    if not len(triangles):
        raise ValueError("polyhedron needs at least one non-degenerate face")
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=points,
        triangles=triangles,
        rgba=color_to_rgba(color, alpha=alpha),
        metadata=metadata or {},
    )


def polyhedron_edges_primitive(
    semantic_id: str,
    vertices: Any,
    faces: Sequence[Sequence[int]],
    color: Any,
    *,
    width_px: float = 1.0,
    alpha: float = 0.40,
) -> LinePrimitive:
    points = np.asarray(vertices, dtype=float)
    edges: set[tuple[int, int]] = set()
    for face in faces:
        indices = [int(value) for value in face]
        for index, first in enumerate(indices):
            second = indices[(index + 1) % len(indices)]
            edges.add(tuple(sorted((first, second))))
    if any(first < 0 or second >= len(points) for first, second in edges):
        raise ValueError("polyhedron face contains an out-of-range vertex")
    return LinePrimitive(
        semantic_id=semantic_id,
        segments=np.asarray(
            [[points[first], points[second]] for first, second in sorted(edges)]
        ),
        rgba=color_to_rgba(color, alpha=alpha),
        width_px=width_px,
        metadata={"kind": "polyhedron_edges"},
    )


def arrow_primitive(
    semantic_id: str,
    origin: Iterable[float],
    end: Iterable[float],
    color: Any,
    *,
    shaft_radius: float = 0.08,
    head_length: float | None = None,
    head_radius_ratio: float = 2.2,
    head_length_ratio: float = 0.28,
    head_radius: float | None = None,
    sides: int = 12,
    alpha: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> TriangleMeshPrimitive:
    start, tip = _point(origin, name="origin"), _point(end, name="end")
    axis = tip - start
    length = float(np.linalg.norm(axis))
    if length < 1e-12:
        raise ValueError("arrow length must be positive")
    direction = axis / length
    shaft_radius = _positive(shaft_radius, name="shaft_radius")
    if head_length is None:
        head_length_ratio = float(head_length_ratio)
        if not np.isfinite(head_length_ratio) or not 0.0 < head_length_ratio < 1.0:
            raise ValueError("head_length_ratio must lie in (0, 1)")
        head_length = length * head_length_ratio
    head_length = float(head_length)
    if not np.isfinite(head_length) or not 0.0 < head_length < length:
        raise ValueError("head_length must lie between zero and arrow length")
    if head_radius is None:
        head_radius = shaft_radius * float(head_radius_ratio)
    head_radius = float(head_radius)
    if not np.isfinite(head_radius) or head_radius < shaft_radius:
        raise ValueError("head_radius must be finite and >= shaft_radius")
    shoulder = tip - direction * head_length
    shaft_vertices, shaft_triangles, shaft_normals = cylinder_mesh(
        start, shoulder, shaft_radius, sides=sides, capped=True
    )
    reference = np.array([0.0, 0.0, 1.0])
    if abs(float(direction @ reference)) > 0.92:
        reference = np.array([0.0, 1.0, 0.0])
    radial_u = np.cross(direction, reference)
    radial_u /= np.linalg.norm(radial_u)
    radial_v = np.cross(direction, radial_u)
    angles = np.linspace(0.0, 2.0 * math.pi, int(sides), endpoint=False)
    ring_normals = (
        np.cos(angles)[:, None] * radial_u + np.sin(angles)[:, None] * radial_v
    )
    base = shoulder + head_radius * ring_normals
    cone_vertices = np.vstack((base, tip))
    cone_normals = np.vstack((ring_normals, direction))
    tip_index = len(base)
    cone_triangles = np.asarray(
        [[index, tip_index, (index + 1) % len(base)] for index in range(len(base))],
        dtype=np.int64,
    )
    offset = len(shaft_vertices)
    return TriangleMeshPrimitive(
        semantic_id=semantic_id,
        vertices=np.vstack((shaft_vertices, cone_vertices)),
        triangles=np.vstack((shaft_triangles, cone_triangles + offset)),
        vertex_normals=np.vstack((shaft_normals, cone_normals)),
        rgba=color_to_rgba(color, alpha=alpha),
        metadata={"kind": "vector", **dict(metadata or {})},
    )


def triangulate_faces(faces: Sequence[Sequence[int]], vertex_count: int) -> np.ndarray:
    triangles: list[list[int]] = []
    for face in faces:
        indices = [int(value) for value in face]
        if len(indices) < 3:
            continue
        if min(indices) < 0 or max(indices) >= int(vertex_count):
            raise ValueError("face contains an out-of-range vertex")
        for index in range(1, len(indices) - 1):
            triangles.append([indices[0], indices[index], indices[index + 1]])
    return np.asarray(triangles, dtype=np.int64).reshape(-1, 3)


def _chi_square_radius(probability: float) -> float:
    probability = float(probability)
    if not 0.0 < probability < 1.0:
        raise ValueError("probability must lie in (0, 1)")

    def chi3_cdf(radius: float) -> float:
        return math.erf(radius / math.sqrt(2.0)) - math.sqrt(
            2.0 / math.pi
        ) * radius * math.exp(-0.5 * radius * radius)

    lower, upper = 0.0, 1.0
    while chi3_cdf(upper) < probability:
        upper *= 2.0
    # Monotone bisection is deterministic and reaches substantially better
    # than double-precision visual tolerances without a SciPy dependency.
    for _ in range(80):
        middle = 0.5 * (lower + upper)
        if chi3_cdf(middle) < probability:
            lower = middle
        else:
            upper = middle
    return 0.5 * (lower + upper)


def _point(value: Iterable[float], *, name: str) -> np.ndarray:
    point = np.asarray(value, dtype=float)
    if point.shape != (3,) or not np.all(np.isfinite(point)):
        raise ValueError(f"{name} must be a finite three-dimensional point")
    return point


def _positive(value: float, *, name: str) -> float:
    result = float(value)
    if not np.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result




def _json_safe(value: Any) -> Any:
    """Convert common NumPy/container values to JSON primitives.

    Scene entities are persisted by :func:`scene_json`; keeping this small
    normaliser here means callers can pass NumPy metadata (for example an
    ``axis_cartesian`` array) without turning an otherwise valid entity into
    a non-serialisable object.  Unknown scalar objects are left untouched so
    Plotly can provide its usual contextual validation error if one is used.
    """

    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _opacity(value: Any, *, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number in [0, 1]") from exc
    if not np.isfinite(out) or out < 0.0 or out > 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return out


def _normalise_edges(
    edges: Iterable[Iterable[int]],
    vertex_count: int,
    *,
    name: str = "edges",
) -> np.ndarray:
    """Validate an explicit edge list and return an ``N×2`` integer array.

    Mesh faces are validated separately because they may be polygons.  Edge
    lists, however, are often hand-written for an open channel and must not
    be allowed to leak an ``IndexError`` from the trace builder.  Keeping the
    checks here gives both the convenience builders and raw scene payloads the
    same failure semantics.
    """

    if isinstance(edges, (str, bytes)):
        raise ValueError(f"{name} must be an iterable of index pairs")
    try:
        rows = list(edges)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of index pairs") from exc

    normalised: list[list[int]] = []
    for edge_index, raw_edge in enumerate(rows):
        try:
            values = list(raw_edge)
        except TypeError as exc:
            raise ValueError(f"{name}[{edge_index}] is not an index pair") from exc
        if len(values) != 2:
            raise ValueError(f"{name}[{edge_index}] must contain exactly two indices")
        pair: list[int] = []
        for value in values:
            if isinstance(value, (bool, np.bool_)):
                raise ValueError(f"{name}[{edge_index}] contains a non-integer index")
            try:
                integer = int(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"{name}[{edge_index}] contains a non-integer index"
                ) from exc
            if isinstance(value, (float, np.floating)) and float(value) != integer:
                raise ValueError(f"{name}[{edge_index}] contains a non-integer index")
            if integer < 0 or integer >= vertex_count:
                raise ValueError(
                    f"{name}[{edge_index}] contains an index outside vertices"
                )
            pair.append(integer)
        if pair[0] == pair[1]:
            raise ValueError(f"{name}[{edge_index}] is degenerate")
        normalised.append(pair)

    return np.asarray(normalised, dtype=int).reshape(-1, 2)


def mesh_entity(
    vertices: Iterable[Iterable[float]],
    faces: Iterable[Iterable[int]],
    *,
    name: str = "geometry",
    entity_id: str | None = None,
    color: str = _DEFAULT_COLOR,
    opacity: float = 1.0,
    visible: bool = True,
    flatshading: bool = True,
    lighting: Mapping[str, Any] | None = None,
    show_edges: bool = False,
    edge_color: str | None = None,
    edge_width: float = 2.0,
    edge_opacity: float = 1.0,
    edges: Iterable[Iterable[int]] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe arbitrary mesh entity for a MatterVis scene.

    Parameters
    ----------
    vertices, faces:
        Cartesian vertices (``N×3``) and polygon/triangle index lists.  Polygon
        faces are triangulated with a fan while preserving their winding.
    name, entity_id:
        Human-readable and stable identifiers.  ``entity_id`` is used in trace
        metadata for picking/inspection and may be omitted.
    opacity:
        Surface opacity.  Keep this at ``1.0`` when exact per-pixel occlusion
        against other meshes is required; Plotly's transparent WebGL surfaces
        use approximate trace-level compositing.
    show_edges:
        Add a true 3-D edge trace alongside the surface.  This is useful for
        an open channel or a low-opacity surface and is still depth-tested in
        the 3-D scene (it is not a 2-D overlay).

    Returns
    -------
    dict
        A scene-ready entity.  Arrays are plain lists so the value can be
        serialised through ``scene_json`` without a custom encoder.
    """

    vertex_array, face_array = validate_mesh(vertices, faces)
    opacity_value = _opacity(opacity, name="opacity")
    edge_opacity_value = _opacity(edge_opacity, name="edge_opacity")
    try:
        edge_width_value = float(edge_width)
    except (TypeError, ValueError) as exc:
        raise ValueError("edge_width must be a finite non-negative number") from exc
    if not np.isfinite(edge_width_value) or edge_width_value < 0.0:
        raise ValueError("edge_width must be a finite non-negative number")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")

    edge_array: np.ndarray | None = None
    if edges is not None:
        edge_array = _normalise_edges(edges, len(vertex_array))

    color_value = _DEFAULT_COLOR if color is None else str(color)
    edge_color_value = color_value if edge_color is None else str(edge_color)
    entity: dict[str, Any] = {
        "kind": "mesh",
        "name": name,
        "vertices": vertex_array.tolist(),
        "faces": face_array.tolist(),
        "color": color_value,
        "opacity": opacity_value,
        "visible": bool(visible),
        "flatshading": bool(flatshading),
        "show_edges": bool(show_edges),
        "edge_color": edge_color_value,
        "edge_width": edge_width_value,
        "edge_opacity": edge_opacity_value,
    }
    if edge_array is not None:
        entity["edges"] = edge_array.tolist()
    if entity_id is not None:
        entity["id"] = str(entity_id)
    if lighting is not None:
        entity["lighting"] = _json_safe(lighting)
    if meta is not None:
        entity["meta"] = _json_safe(meta)
    return entity


def cylinder_entity(
    center: Iterable[float],
    axis: Iterable[float],
    radius: float,
    length: float,
    *,
    segments: int = 32,
    caps: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a scene-ready cylinder mesh.

    The default is an *open* cylinder (no end caps), which is the least
    misleading representation of a through-channel: looking along the axis
    leaves both openings visible instead of placing an opaque disk across the
    hole.  Pass ``caps=True`` for a solid cylinder entity.
    """

    vertices, faces = cylinder_vertices_faces(
        center,
        axis,
        radius,
        length,
        segments=segments,
        caps=caps,
    )
    custom_edges = kwargs.pop("edges", None)
    side_edges = []
    segment_count = (len(vertices) - (2 if caps else 0)) // 2
    for index in range(segment_count):
        nxt = (index + 1) % segment_count
        side_edges.extend(
            [
                [index, nxt],
                [segment_count + index, segment_count + nxt],
            ]
        )
    # A seam at every polygon segment makes a high-resolution cylinder read
    # like a striped sheet in a side view.  Keep a few deterministic seams for
    # orientation/depth cues while leaving the surface itself responsible for
    # the silhouette and occlusion.
    seam_count = min(4, segment_count)
    seam_indices = np.linspace(0, segment_count - 1, seam_count, dtype=int)
    side_edges.extend([[index, segment_count + index] for index in seam_indices])
    return mesh_entity(
        vertices,
        faces,
        edges=side_edges if custom_edges is None else custom_edges,
        **kwargs,
    )


def implicit_entity(
    field: Any,
    bounds: Iterable[Iterable[float]],
    *,
    level: float = 0.0,
    resolution: int | Iterable[int] = 32,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a real 3-D mesh entity from an arbitrary implicit surface.

    ``field`` may be a vectorised ``field(points)`` callable, a broadcastable
    ``field(x, y, z)`` callable, or a scalar xyz function.  ``bounds`` is the
    finite Cartesian clipping box used to sample the otherwise potentially
    unbounded surface.  The callable is evaluated immediately and is not
    retained in the scene payload, so the returned entity remains JSON-safe
    and independent of the caller's project or runtime.

    The extracted triangles are passed through :func:`mesh_entity`; rendering
    therefore uses the same depth-tested Plotly ``Mesh3d`` path as atoms,
    bonds, BFDH facets, and coordination polyhedra.  A plane, sphere, torus,
    signed-distance primitive, or a project-specific field all use this same
    entry point without changes to the renderer.
    """

    # Materialise iterators once: the sampler and metadata both need the
    # domain/resolution, and consuming a generator twice would otherwise
    # produce an empty or misleading payload.
    try:
        bounds_value = np.asarray(list(bounds), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("bounds must be an iterable of three finite pairs") from exc
    if np.isscalar(resolution):
        resolution_value = resolution
    else:
        try:
            resolution_value = tuple(resolution)
        except TypeError as exc:
            raise ValueError(
                "resolution must be an integer or three integers >= 2"
            ) from exc

    vertices, faces = implicit_surface_mesh(
        field,
        bounds_value,
        level=level,
        resolution=resolution_value,
    )
    if np.isscalar(resolution_value):
        resolution_meta = [int(resolution_value)] * 3
    else:
        resolution_meta = [int(value) for value in resolution_value]
    user_meta = kwargs.pop("meta", None)
    generated_meta: dict[str, Any] = {
        "implicit": True,
        "level": float(level),
        "bounds": bounds_value.tolist(),
        "resolution": resolution_meta,
    }
    if isinstance(user_meta, Mapping):
        generated_meta.update(_json_safe(user_meta))
    kwargs["meta"] = generated_meta
    return mesh_entity(vertices, faces, **kwargs)


def through_cylinder_entity(
    lattice: Iterable[Iterable[float]],
    direction_hkl: Iterable[int],
    radius: float,
    *,
    center_frac: Iterable[float] = (0.5, 0.5, 0.5),
    periods: float = 1.0,
    segments: int = 32,
    caps: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a cylinder aligned with a crystallographic lattice direction.

    ``lattice`` uses MatterVis' row-vector convention: fractional
    coordinates map to Cartesian coordinates as ``frac @ lattice``.  The
    reduced Miller/index direction is converted to the Cartesian vector
    ``h*a + k*b + l*c`` before constructing the mesh.  This convenience layer
    keeps the direction and centre used for rendering identical to a
    through-cylinder operation supplied by MolCrysKit and avoids accidental
    ``[110]``/``[100]`` mismatches in caller scripts.

    ``periods`` controls the physical length in repeats of the direction
    vector; the default one-period open cylinder is suitable for a periodic
    channel.  Use ``caps=True`` when the entity represents a solid rather
    than a void wall.
    """

    try:
        lattice_array = np.asarray(lattice, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("lattice must be a finite 3×3 numeric array") from exc
    if (
        lattice_array.shape != (3, 3)
        or not np.all(np.isfinite(lattice_array))
        or abs(float(np.linalg.det(lattice_array))) <= 1e-12
    ):
        raise ValueError("lattice must be a non-singular finite 3×3 array")

    try:
        direction_array = np.asarray(list(direction_hkl), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("direction_hkl must contain three integers") from exc
    if (
        direction_array.shape != (3,)
        or not np.all(np.isfinite(direction_array))
        or not np.allclose(direction_array, np.rint(direction_array))
    ):
        raise ValueError("direction_hkl must contain three integers")
    direction = np.rint(direction_array).astype(int)
    if not np.any(direction):
        raise ValueError("direction_hkl must be non-zero")
    divisor = math.gcd(*(abs(int(value)) for value in direction))
    direction //= divisor
    first_nonzero = int(direction[np.flatnonzero(direction)[0]])
    if first_nonzero < 0:
        direction *= -1

    try:
        center_frac_array = np.asarray(list(center_frac), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("center_frac must contain three finite numbers") from exc
    if center_frac_array.shape != (3,) or not np.all(np.isfinite(center_frac_array)):
        raise ValueError("center_frac must contain three finite numbers")
    try:
        periods_value = float(periods)
    except (TypeError, ValueError) as exc:
        raise ValueError("periods must be a finite positive number") from exc
    if not np.isfinite(periods_value) or periods_value <= 0.0:
        raise ValueError("periods must be a finite positive number")

    axis = direction.astype(float) @ lattice_array
    length = float(np.linalg.norm(axis)) * periods_value
    center = center_frac_array @ lattice_array
    entity = cylinder_entity(
        center=center,
        axis=axis,
        radius=radius,
        length=length,
        segments=segments,
        caps=caps,
        **kwargs,
    )
    entity_meta = dict(entity.get("meta") or {})
    entity_meta.update(
        {
            "direction_hkl": direction.tolist(),
            "center_frac": center_frac_array.tolist(),
            "axis_cartesian": axis.tolist(),
            "periods": periods_value,
            "length_A": length,
        }
    )
    entity["meta"] = entity_meta
    return entity


def validate_geometry_style(scene: Mapping[str, Any], style: Mapping[str, Any]) -> None:
    """Reject render modes that cannot provide 3-D geometry occlusion.

    The ``flat`` and ``flat + ortep`` paths deliberately use 2-D/billboard
    primitives.  Accepting a mesh entity in those paths would silently drop
    its depth relationship (or make it look like a paper overlay), which is
    exactly the failure mode this API is intended to prevent.  Callers that
    attach geometry therefore have to select the real ``Mesh3d`` material.
    """

    entities = scene.get("geometry_entities")
    if entities is None:
        return
    if not isinstance(entities, (list, tuple)):
        raise ValueError('scene["geometry_entities"] must be a list of mesh entities')
    if not entities:
        return
    if str(style.get("material", "mesh")) != "mesh":
        raise ValueError(
            "scene geometry_entities require material='mesh'; "
            "the flat/billboard renderer cannot provide 3-D occlusion"
        )


def _entity_edges(
    faces: np.ndarray,
    explicit_edges: Any = None,
    *,
    vertex_count: int,
) -> list[tuple[int, int]]:
    """Return unique undirected mesh edges in deterministic order."""

    if explicit_edges is not None:
        validated = _normalise_edges(explicit_edges, vertex_count)
        return sorted(
            {tuple(sorted((int(edge[0]), int(edge[1])))) for edge in validated}
        )

    edges: set[tuple[int, int]] = set()
    for a, b, c in np.asarray(faces, dtype=int):
        edges.update(
            {
                tuple(sorted((int(a), int(b)))),
                tuple(sorted((int(b), int(c)))),
                tuple(sorted((int(c), int(a)))),
            }
        )
    return sorted(edges)


def _trace_meta(entity: Mapping[str, Any], role: str) -> dict[str, Any]:
    raw = entity.get("meta")
    meta = dict(raw) if isinstance(raw, Mapping) else {}
    meta["mv_role"] = role
    meta.setdefault("kind", "geometry_entity")
    if entity.get("id") is not None:
        meta["geometry_id"] = str(entity["id"])
    return meta


def geometry_entity_traces(scene: Mapping[str, Any]) -> list[go.BaseTraceType]:
    """Build Plotly traces for ``scene["geometry_entities"]``.

    Every surface is a genuine ``Mesh3d`` trace in world coordinates.  No
    projection to paper coordinates occurs here, so Plotly can resolve depth
    against atoms and other meshes.  Invalid hand-written scene entries raise
    a contextual ``ValueError``; callers using :func:`mesh_entity` get the
    same validation before the scene is assembled.
    """

    entities = scene.get("geometry_entities")
    if entities is None:
        return []
    if not isinstance(entities, (list, tuple)):
        raise ValueError('scene["geometry_entities"] must be a list of mesh entities')

    import plotly.graph_objects as go

    traces: list[go.BaseTraceType] = []
    for entity_index, raw_entity in enumerate(entities):
        if not isinstance(raw_entity, Mapping):
            raise ValueError(f"geometry_entities[{entity_index}] must be a mapping")
        if not bool(raw_entity.get("visible", True)):
            continue
        try:
            vertices, faces = validate_mesh(
                raw_entity.get("vertices"), raw_entity.get("faces")
            )
            opacity_value = _opacity(raw_entity.get("opacity", 1.0), name="opacity")
            raw_color = raw_entity.get("color", _DEFAULT_COLOR)
            color = _DEFAULT_COLOR if raw_color is None else str(raw_color)
            name = str(raw_entity.get("name", raw_entity.get("id", "geometry")))
            mesh_kwargs: dict[str, Any] = {
                "x": vertices[:, 0],
                "y": vertices[:, 1],
                "z": vertices[:, 2],
                "i": faces[:, 0],
                "j": faces[:, 1],
                "k": faces[:, 2],
                "color": color,
                "opacity": opacity_value,
                "flatshading": bool(raw_entity.get("flatshading", True)),
                "hoverinfo": "skip",
                "showlegend": False,
                "name": name,
                "meta": _json_safe(_trace_meta(raw_entity, "geometry_entity")),
            }
            lighting = raw_entity.get("lighting")
            if isinstance(lighting, Mapping):
                mesh_kwargs["lighting"] = dict(lighting)
            traces.append(go.Mesh3d(**mesh_kwargs))

            if bool(raw_entity.get("show_edges", False)):
                edge_list = _entity_edges(
                    faces,
                    raw_entity.get("edges"),
                    vertex_count=len(vertices),
                )
                if edge_list:
                    xs: list[float | None] = []
                    ys: list[float | None] = []
                    zs: list[float | None] = []
                    for start, end in edge_list:
                        xs.extend(
                            [float(vertices[start, 0]), float(vertices[end, 0]), None]
                        )
                        ys.extend(
                            [float(vertices[start, 1]), float(vertices[end, 1]), None]
                        )
                        zs.extend(
                            [float(vertices[start, 2]), float(vertices[end, 2]), None]
                        )
                    edge_opacity_value = _opacity(
                        raw_entity.get("edge_opacity", 1.0), name="edge_opacity"
                    )
                    try:
                        edge_width = float(raw_entity.get("edge_width", 2.0))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            "edge_width must be a finite non-negative number"
                        ) from exc
                    if not np.isfinite(edge_width) or edge_width < 0:
                        raise ValueError(
                            "edge_width must be a finite non-negative number"
                        )
                    traces.append(
                        go.Scatter3d(
                            x=xs,
                            y=ys,
                            z=zs,
                            mode="lines",
                            line={
                                "color": str(raw_entity.get("edge_color") or color),
                                "width": edge_width,
                            },
                            opacity=edge_opacity_value,
                            hoverinfo="skip",
                            showlegend=False,
                            name=f"{name} edges",
                            meta=_json_safe(_trace_meta(raw_entity, "geometry_entity_edge")),
                        )
                    )
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                f"geometry_entities[{entity_index}] is invalid: {exc}"
            ) from exc
    return traces
__all__ = [
    "aromatic_ring_primitive",
    "arrow_primitive",
    "bond_line_primitives",
    "bond_primitives",
    "color_to_rgba",
    "cylinder_mesh",
    "cylinder_primitive",
    "ellipsoid_axes_primitive",
    "ellipsoid_hatch_primitive",
    "ellipsoid_mesh",
    "ellipsoid_principal_axes",
    "ellipsoid_primitive",
    "mesh_primitive",
    "polyhedron_edges_primitive",
    "polyhedron_primitive",
    "sphere_mesh",
    "sphere_primitive",
    "triangulate_faces",
    "unit_cell_primitive",
    "cylinder_entity",
    "geometry_entity_traces",
    "implicit_entity",
    "mesh_entity",
    "through_cylinder_entity",
    "validate_geometry_style",
]
