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


__all__ = [name for name in globals() if not name.startswith("__")]
