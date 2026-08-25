"""Pure NumPy geometry builders shared by every rendering backend."""

from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np

from .contracts import LinePrimitive, RGBA, TriangleMeshPrimitive

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
    alpha: float = 1.0,
    depth_test: bool = True,
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
        depth_test=depth_test,
        metadata={"kind": "unit_cell"},
    )


def polyhedron_primitive(
    semantic_id: str,
    vertices: Any,
    faces: Sequence[Sequence[int]],
    color: Any,
    *,
    alpha: float = 0.55,
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
    alpha: float = 0.9,
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
    head_radius_ratio: float = 2.2,
    head_length_ratio: float = 0.28,
    sides: int = 12,
) -> TriangleMeshPrimitive:
    start, tip = _point(origin, name="origin"), _point(end, name="end")
    axis = tip - start
    length = float(np.linalg.norm(axis))
    if length < 1e-12:
        raise ValueError("arrow length must be positive")
    direction = axis / length
    head_length = length * float(head_length_ratio)
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
    base = shoulder + shaft_radius * float(head_radius_ratio) * ring_normals
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
        rgba=color_to_rgba(color),
        metadata={"kind": "vector"},
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


__all__ = [
    "aromatic_ring_primitive",
    "arrow_primitive",
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
]
