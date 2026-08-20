from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .common import *

def _unit_sphere(lat_steps: int = 9, lon_steps: int = 14) -> Tuple[np.ndarray, np.ndarray]:
    vertices = []
    for lat_idx in range(lat_steps + 1):
        theta = math.pi * lat_idx / lat_steps
        for lon_idx in range(lon_steps):
            phi = 2.0 * math.pi * lon_idx / lon_steps
            vertices.append(
                [
                    math.sin(theta) * math.cos(phi),
                    math.sin(theta) * math.sin(phi),
                    math.cos(theta),
                ]
            )
    triangles = []
    for lat_idx in range(lat_steps):
        for lon_idx in range(lon_steps):
            next_lon = (lon_idx + 1) % lon_steps
            a = lat_idx * lon_steps + lon_idx
            b = lat_idx * lon_steps + next_lon
            c = (lat_idx + 1) * lon_steps + lon_idx
            d = (lat_idx + 1) * lon_steps + next_lon
            triangles.append([a, c, b])
            triangles.append([b, c, d])
    return np.array(vertices, dtype=float), np.array(triangles, dtype=int)


def _append_mesh(mesh: dict, vertices: np.ndarray, triangles: np.ndarray):
    base = len(mesh["x"])
    mesh["x"].extend(vertices[:, 0].tolist())
    mesh["y"].extend(vertices[:, 1].tolist())
    mesh["z"].extend(vertices[:, 2].tolist())
    mesh["i"].extend((triangles[:, 0] + base).tolist())
    mesh["j"].extend((triangles[:, 1] + base).tolist())
    mesh["k"].extend((triangles[:, 2] + base).tolist())


def _sphere_mesh(center: Iterable[float], radius: float, lat_steps: int = 9, lon_steps: int = 14):
    unit_vertices, unit_triangles = _unit_sphere(lat_steps=lat_steps, lon_steps=lon_steps)
    center = np.array(center, dtype=float)
    vertices = unit_vertices * float(radius) + center[None, :]
    return vertices, unit_triangles


def _sphere_mesh_batch(centers: Iterable[Iterable[float]], radii: Iterable[float], lat_steps: int = 9, lon_steps: int = 14):
    centers_arr = np.asarray(list(centers), dtype=float).reshape(-1, 3)
    radii_arr = np.asarray(list(radii), dtype=float).reshape(-1)
    if len(centers_arr) == 0:
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    unit_vertices, unit_triangles = _unit_sphere(lat_steps=lat_steps, lon_steps=lon_steps)
    vertices = unit_vertices[None, :, :] * radii_arr[:, None, None] + centers_arr[:, None, :]
    n_unit_vertices = len(unit_vertices)
    triangles = unit_triangles[None, :, :] + (np.arange(len(centers_arr)) * n_unit_vertices)[:, None, None]
    return vertices.reshape(-1, 3), triangles.reshape(-1, 3)


def _cylinder_mesh(p0: Iterable[float], p1: Iterable[float], radius: float, sides: int = 8):
    start = np.array(p0, dtype=float)
    end = np.array(p1, dtype=float)
    axis = end - start
    length = np.linalg.norm(axis)
    if length < 1e-8:
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    axis /= length
    ref = np.array([0.0, 0.0, 1.0], dtype=float)
    if abs(np.dot(axis, ref)) > 0.92:
        ref = np.array([0.0, 1.0, 0.0], dtype=float)
    u = np.cross(axis, ref)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)

    ring0 = []
    ring1 = []
    for idx in range(sides):
        ang = 2.0 * math.pi * idx / sides
        offset = math.cos(ang) * u * radius + math.sin(ang) * v * radius
        ring0.append(start + offset)
        ring1.append(end + offset)
    vertices = np.array(ring0 + ring1 + [start, end], dtype=float)
    cap0 = len(vertices) - 2
    cap1 = len(vertices) - 1
    triangles = []
    for idx in range(sides):
        nxt = (idx + 1) % sides
        a0 = idx
        a1 = nxt
        b0 = idx + sides
        b1 = nxt + sides
        triangles.extend([[a0, b0, a1], [a1, b0, b1], [cap0, a1, a0], [cap1, b0, b1]])
    return vertices, np.array(triangles, dtype=int)


def _cylinder_mesh_batch(segments, radius: float, sides: int = 8):
    segments = list(segments)
    if not segments:
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    starts = np.asarray([seg[0] for seg in segments], dtype=float)
    ends = np.asarray([seg[1] for seg in segments], dtype=float)
    axes = ends - starts
    lengths = np.linalg.norm(axes, axis=1)
    valid = lengths >= 1e-8
    if not np.any(valid):
        return np.zeros((0, 3), dtype=float), np.zeros((0, 3), dtype=int)
    starts = starts[valid]
    ends = ends[valid]
    axes = axes[valid] / lengths[valid, None]

    refs = np.tile(np.array([0.0, 0.0, 1.0], dtype=float), (len(axes), 1))
    refs[np.abs(axes @ np.array([0.0, 0.0, 1.0], dtype=float)) > 0.92] = np.array([0.0, 1.0, 0.0])
    u = np.cross(axes, refs)
    u /= np.linalg.norm(u, axis=1)[:, None]
    v = np.cross(axes, u)

    angles = np.linspace(0.0, 2.0 * math.pi, int(sides), endpoint=False)
    offsets = (
        np.cos(angles)[None, :, None] * u[:, None, :]
        + np.sin(angles)[None, :, None] * v[:, None, :]
    ) * float(radius)
    ring0 = starts[:, None, :] + offsets
    ring1 = ends[:, None, :] + offsets
    vertices = np.concatenate([ring0, ring1, starts[:, None, :], ends[:, None, :]], axis=1)

    local_tris = []
    cap0 = 2 * int(sides)
    cap1 = cap0 + 1
    for idx in range(int(sides)):
        nxt = (idx + 1) % int(sides)
        a0 = idx
        a1 = nxt
        b0 = idx + int(sides)
        b1 = nxt + int(sides)
        local_tris.extend([[a0, b0, a1], [a1, b0, b1], [cap0, a1, a0], [cap1, b0, b1]])
    local_tris_arr = np.asarray(local_tris, dtype=int)
    n_vertices_per_segment = 2 * int(sides) + 2
    triangles = local_tris_arr[None, :, :] + (np.arange(len(starts)) * n_vertices_per_segment)[:, None, None]
    return vertices.reshape(-1, 3), triangles.reshape(-1, 3)


def arrow_mesh_geometry(
    origin,
    end,
    *,
    shaft_radius: float = 0.10,
    head_length: float | None = None,
    head_length_ratio: float = 0.28,
    head_radius: float | None = None,
    head_radius_ratio: float = 2.2,
    sides: int = 12,
) -> tuple[np.ndarray, np.ndarray]:
    """Return one opaque, watertight shaft-and-cone arrow mesh.

    The shaft/head junction uses a shoulder annulus rather than overlapping
    cylinder and cone caps, preventing z-fighting at the colour-continuous
    seam. Coordinates are world-space Cartesian values.
    """
    start = np.asarray(origin, dtype=float)
    tip = np.asarray(end, dtype=float)
    if start.shape != (3,) or tip.shape != (3,):
        raise ValueError("origin and end must each contain three values")
    if not np.all(np.isfinite(start)) or not np.all(np.isfinite(tip)):
        raise ValueError("origin and end must be finite")
    axis = tip - start
    length = float(np.linalg.norm(axis))
    if length <= 1.0e-10:
        raise ValueError("arrow length must be positive")
    shaft_radius = float(shaft_radius)
    if not np.isfinite(shaft_radius) or shaft_radius <= 0.0:
        raise ValueError("shaft_radius must be positive")
    if int(sides) != sides or int(sides) < 3:
        raise ValueError("sides must be an integer >= 3")
    sides = int(sides)
    if head_length is None:
        if not np.isfinite(head_length_ratio) or not 0.0 < head_length_ratio < 1.0:
            raise ValueError("head_length_ratio must lie in (0, 1)")
        head_length = length * float(head_length_ratio)
    head_length = float(head_length)
    if not np.isfinite(head_length) or not 0.0 < head_length < length:
        raise ValueError("head_length must lie between zero and arrow length")
    if head_radius is None:
        head_radius = shaft_radius * float(head_radius_ratio)
    head_radius = float(head_radius)
    if not np.isfinite(head_radius) or head_radius < shaft_radius:
        raise ValueError("head_radius must be finite and >= shaft_radius")

    direction = axis / length
    reference = np.array([0.0, 0.0, 1.0], dtype=float)
    if abs(float(direction @ reference)) > 0.92:
        reference = np.array([0.0, 1.0, 0.0], dtype=float)
    radial_u = np.cross(direction, reference)
    radial_u /= np.linalg.norm(radial_u)
    radial_v = np.cross(direction, radial_u)
    angles = np.linspace(0.0, 2.0 * math.pi, sides, endpoint=False)
    unit_ring = (
        np.cos(angles)[:, None] * radial_u[None, :]
        + np.sin(angles)[:, None] * radial_v[None, :]
    )
    shoulder = tip - head_length * direction
    shaft_start_ring = start[None, :] + shaft_radius * unit_ring
    shaft_end_ring = shoulder[None, :] + shaft_radius * unit_ring
    head_base_ring = shoulder[None, :] + head_radius * unit_ring
    vertices = np.vstack([shaft_start_ring, shaft_end_ring, head_base_ring, start, tip])
    origin_index = 3 * sides
    tip_index = origin_index + 1
    triangles: list[list[int]] = []
    for index in range(sides):
        nxt = (index + 1) % sides
        start_i, start_n = index, nxt
        shaft_i, shaft_n = sides + index, sides + nxt
        head_i, head_n = 2 * sides + index, 2 * sides + nxt
        triangles.extend(
            [
                [start_i, shaft_i, start_n],
                [start_n, shaft_i, shaft_n],
                [origin_index, start_n, start_i],
                [shaft_i, head_i, shaft_n],
                [shaft_n, head_i, head_n],
                [head_i, tip_index, head_n],
            ]
        )
    return vertices, np.asarray(triangles, dtype=int)


__all__ = [name for name in globals() if not name.startswith("__")]
