"""Taubin smoothing for one closed or boundary-cut isosurface mesh.

Positive and negative phases are smoothed separately by the caller. This
module never welds meshes together. Smoothing is a visual filter: it moves
vertices after the isovalue has already been extracted and does not change
which voxels are inside the surface.

The publication default is Taubin (λ/μ), not plain Laplacian shrinkage and
not Humphrey/HC.
"""

from __future__ import annotations

import numpy as np

# Classic Taubin low-pass pair. μ is negative so the second step undoes the
# shrinkage of the λ step instead of pinching a lobe waist.
TAUBIN_LAMBDA = 0.5
TAUBIN_MU = -0.53
TAUBIN_ITERATIONS = 15
_BOUNDARY_INDEX_TOLERANCE = 1.0e-4
_GRADIENT_INDEX_TOLERANCE = 1.0e-5
_GRADIENT_NORM_EPSILON = 1.0e-8


def boundary_vertex_mask(index_vertices: np.ndarray, grid_shape: tuple[int, ...]) -> np.ndarray:
    """True for vertices that sit on the marched grid box.

    Those vertices are the cube-boundary cut. Taubin leaves them pinned so
    the cut does not crawl inward.
    """

    points = np.asarray(index_vertices, dtype=float)
    upper = np.asarray(grid_shape, dtype=float) - 1.0
    on_face = (points <= _BOUNDARY_INDEX_TOLERANCE) | (
        points >= upper - _BOUNDARY_INDEX_TOLERANCE
    )
    return np.any(on_face, axis=1)


def _neighbor_index(
    vertex_count: int, triangles: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    faces = np.asarray(triangles, dtype=np.int64)
    if len(faces) == 0 or vertex_count == 0:
        return (
            np.empty(0, dtype=np.int64),
            np.zeros(vertex_count + 1, dtype=np.int64),
        )
    pairs = np.vstack(
        (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])
    )
    edges = np.vstack((pairs, pairs[:, ::-1]))
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges = edges[order]
    keep = np.empty(len(edges), dtype=bool)
    keep[0] = True
    keep[1:] = np.any(edges[1:] != edges[:-1], axis=1)
    edges = edges[keep]
    counts = np.bincount(edges[:, 0], minlength=vertex_count)
    offsets = np.zeros(vertex_count + 1, dtype=np.int64)
    np.cumsum(counts, out=offsets[1:])
    return edges[:, 1], offsets


def taubin_smooth(
    vertices: np.ndarray,
    triangles: np.ndarray,
    *,
    pinned: np.ndarray | None = None,
    iterations: int = TAUBIN_ITERATIONS,
    lam: float = TAUBIN_LAMBDA,
    mu: float = TAUBIN_MU,
) -> np.ndarray:
    """Return smoothed vertex positions. Faces are not modified.

    ``pinned`` vertices, including the cube-box cut, stay where they are.
    """

    points = np.array(vertices, dtype=float, copy=True)
    steps = int(iterations)
    if steps <= 0 or len(points) == 0 or len(triangles) == 0:
        return points
    if lam <= 0.0 or mu >= 0.0:
        raise ValueError("Taubin smoothing expects lam > 0 and mu < 0")
    neighbors, offsets = _neighbor_index(len(points), triangles)
    counts = np.diff(offsets)
    if pinned is None:
        fixed = np.zeros(len(points), dtype=bool)
    else:
        fixed = np.asarray(pinned, dtype=bool)
        if fixed.shape != (len(points),):
            raise ValueError("pinned mask must have one entry per vertex")
    owned = np.repeat(np.arange(len(points)), counts)
    for _ in range(steps):
        for weight in (float(lam), float(mu)):
            summed = np.zeros_like(points)
            if len(neighbors):
                np.add.at(summed, owned, points[neighbors])
            degree = np.maximum(counts.astype(float), 1.0)
            delta = summed / degree[:, None] - points
            delta[counts == 0] = 0.0
            delta[fixed] = 0.0
            points = points + weight * delta
    return points


def area_weighted_vertex_normals(
    vertices: np.ndarray,
    triangles: np.ndarray,
    *,
    orient_like: np.ndarray | None = None,
) -> np.ndarray:
    """Area-weighted vertex normals in the same frame as ``vertices``.

    ``orient_like`` flips the whole field when the new winding disagrees
    with a reference, so a phase does not invert relative to marching cubes.
    """

    points = np.asarray(vertices, dtype=float)
    faces = np.asarray(triangles, dtype=np.int64)
    normals = np.zeros_like(points)
    if len(points) == 0 or len(faces) == 0:
        return normals
    corners = points[faces]
    face_normals = np.cross(
        corners[:, 1] - corners[:, 0],
        corners[:, 2] - corners[:, 0],
    )
    for corner in range(3):
        np.add.at(normals, faces[:, corner], face_normals)
    if orient_like is not None:
        reference = np.asarray(orient_like, dtype=float)
        if reference.shape == normals.shape:
            dots = np.sum(normals * reference, axis=1)
            usable = np.linalg.norm(reference, axis=1) > 1.0e-8
            if np.any(usable) and float(np.mean(dots[usable])) < 0.0:
                normals = -normals
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1.0e-12
    normals[valid] /= lengths[valid, None]
    return normals


def _sample_trilinear(field: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sample a scalar or vector grid. ``field`` is ``(Nx, Ny, Nz)`` or ``(..., C)``."""

    spatial = np.asarray(field.shape[:3], dtype=int)
    upper = spatial - 1
    inside = np.all(
        (points >= -_GRADIENT_INDEX_TOLERANCE)
        & (points <= upper + _GRADIENT_INDEX_TOLERANCE),
        axis=1,
    )
    clipped = np.clip(points, 0.0, upper.astype(float))
    base = np.floor(clipped).astype(np.int64)
    nxt = np.minimum(base + 1, upper)
    weight = clipped - base
    weight = np.where(base == upper, 0.0, weight)
    x0, y0, z0 = base[:, 0], base[:, 1], base[:, 2]
    x1, y1, z1 = nxt[:, 0], nxt[:, 1], nxt[:, 2]
    tx, ty, tz = weight[:, 0], weight[:, 1], weight[:, 2]

    def mix(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
        scale = t[:, None] if a.ndim == 2 else t
        return a * (1.0 - scale) + b * scale

    c00 = mix(field[x0, y0, z0], field[x1, y0, z0], tx)
    c01 = mix(field[x0, y0, z1], field[x1, y0, z1], tx)
    c10 = mix(field[x0, y1, z0], field[x1, y1, z0], tx)
    c11 = mix(field[x0, y1, z1], field[x1, y1, z1], tx)
    c0 = mix(c00, c10, ty)
    c1 = mix(c01, c11, ty)
    return mix(c0, c1, tz), inside


def scalar_gradient_normals(
    values: np.ndarray,
    index_vertices: np.ndarray,
    *,
    world_from_index: np.ndarray,
    fallback: np.ndarray,
) -> np.ndarray:
    """World normals from the scalar gradient at smoothed index positions.

    Central differences (``numpy.gradient``) are sampled with trilinear
    interpolation. The index gradient is mapped by ``(J^{-1})^T``, where
    ``J`` sends index rows to world rows through ``world_from_index``.
    Vertices outside the grid, or where ``||∇f||`` is tiny, keep
    ``fallback`` so a flat sample does not invent a direction. The gradient
    samples are flipped together when they disagree with that fallback,
    which follows the marching-cubes face winding.
    """

    fallback_normals = np.asarray(fallback, dtype=float)
    points = np.asarray(index_vertices, dtype=float)
    if len(points) == 0:
        return fallback_normals
    components = np.stack(np.gradient(np.asarray(values, dtype=float)), axis=-1)
    sampled, inside = _sample_trilinear(components, points)
    basis = np.asarray(world_from_index, dtype=float)
    world = sampled @ np.linalg.inv(basis).T
    world_lengths = np.linalg.norm(world, axis=1)
    magnitude = np.linalg.norm(sampled, axis=1)
    usable = (
        inside
        & np.all(np.isfinite(world), axis=1)
        & (world_lengths > 1.0e-12)
        & np.isfinite(magnitude)
        & (magnitude > _GRADIENT_NORM_EPSILON)
    )
    normals = np.array(fallback_normals, copy=True)
    if np.any(usable):
        unit = world[usable] / world_lengths[usable, None]
        normals[usable] = unit
        reference = fallback_normals[usable]
        vote = np.linalg.norm(reference, axis=1) > 1.0e-8
        if np.any(vote):
            agreement = np.sum(unit[vote] * reference[vote], axis=1)
            if float(np.mean(agreement)) < 0.0:
                normals[usable] *= -1.0
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1.0e-12
    normals[valid] /= lengths[valid, None]
    return normals


__all__ = [
    "TAUBIN_ITERATIONS",
    "TAUBIN_LAMBDA",
    "TAUBIN_MU",
    "area_weighted_vertex_normals",
    "boundary_vertex_mask",
    "scalar_gradient_normals",
    "taubin_smooth",
]
