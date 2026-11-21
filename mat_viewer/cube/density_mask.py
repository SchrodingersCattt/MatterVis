"""Optional high-density ceiling for an interaction isosurface.

The mask is off unless the caller turns it on. It samples a density cube at
mesh vertices and clips triangles to ρ ≤ ρ_cut. A mixed triangle is cut
where the sampled field crosses ρ_cut. An edge longer than one density
voxel is split first, so the cutoff is not the chord between the two
marching-cubes samples. Companion arrays use that same parameter.
Deleting the whole triangle would move the hole with the marching-cubes
stride. This module does not rewrite the field cube, the density cube, or a
colour cube, and it does not choose the cutoff. The arrays it returns are
the visible mesh. Smoothing and camera fit, when they run, start from those
vertices rather than from the unfiltered triangulation.

ρ and ρ_cut are in the density cube's scalar values. For electron density
those values are atomic units, e/Bohr³. This module does not convert e/Å³.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .color_field import _other_index_at_mesh_indices, sample_cube_at_mesh_indices
from .io import CubeData

# 1 a.u. of electron density ≈ 6.748 e/Å³. Documented for callers; not applied.
ELECTRON_DENSITY_AU_PER_ANGSTROM3 = 6.748
# Axis-aligned trilinear density is linear across one voxel. Longer edges
# are split before the cutoff parameter is interpolated.
_MAX_INDEX_SPAN = 1.0


def resolve_density_mask(
    *,
    enabled: bool,
    density_cube: CubeData | None,
    density_cutoff: float | None,
) -> tuple[CubeData, float] | None:
    """Return the cube and cutoff, or None when the mask is off.

    A stored cube and cutoff are ignored while ``enabled`` is false. When
    the mask is on, a missing cube or a cutoff that is not finite and > 0
    is an error. No cutoff is substituted.
    """
    if not enabled:
        return None
    if density_cube is None or density_cutoff is None:
        raise ValueError(
            "Density mask is on, but the density cube or the cutoff is missing. "
            "The isosurface was not masked, and no cutoff was chosen "
            "automatically. Pass a density cube and ρ_cut > 0 in e/Bohr^3."
        )
    cutoff = float(density_cutoff)
    if not np.isfinite(cutoff) or cutoff <= 0.0:
        raise ValueError(
            "Density cutoff must be finite and > 0 in e/Bohr^3 "
            "(atomic units; 1 a.u. ≈ "
            f"{ELECTRON_DENSITY_AU_PER_ANGSTROM3:g} e/Å^3). "
            "Values in e/Å^3 are not accepted or converted."
        )
    values = np.asarray(density_cube.values)
    if values.ndim != 3:
        raise ValueError("density cube values must be a 3-D grid")
    return density_cube, cutoff


def _clip_tokens(vertices: tuple[int, int, int], inside: np.ndarray):
    """Return clipped-triangle tokens, preserving the input winding.

    A token is ``("v", index)`` for a vertex with ρ ≤ cutoff or
    ``("e", i, j)`` for the cutoff point on edge ``i``–``j`` with ``i < j``.
    """

    flags = tuple(bool(inside[index]) for index in vertices)
    count = sum(flags)
    if count == 0:
        return ()
    if count == 3:
        return (tuple(("v", index) for index in vertices),)
    if count == 1:
        pivot = flags.index(True)
        kept = vertices[pivot]
        nxt = vertices[(pivot + 1) % 3]
        prv = vertices[(pivot + 2) % 3]
        return (
            (
                ("v", kept),
                ("e", *sorted((kept, nxt))),
                ("e", *sorted((kept, prv))),
            ),
        )
    outside = flags.index(False)
    dropped = vertices[outside]
    nxt = vertices[(outside + 1) % 3]
    prv = vertices[(outside + 2) % 3]
    near_prv = ("e", *sorted((prv, dropped)))
    near_nxt = ("e", *sorted((dropped, nxt)))
    return (
        (("v", nxt), ("v", prv), near_prv),
        (("v", nxt), near_prv, near_nxt),
    )


def _finite_samples(sampled: np.ndarray) -> np.ndarray:
    values = np.asarray(sampled, dtype=float)
    if values.size and not np.all(np.isfinite(values)):
        raise ValueError(
            "density cube produced a nonfinite sample; the isosurface was "
            "not masked with a replacement cutoff"
        )
    return values


def _sample_density(
    source: CubeData,
    density: CubeData,
    mesh_points: np.ndarray,
    *,
    stride: int,
) -> np.ndarray:
    sampled = sample_cube_at_mesh_indices(
        source,
        density,
        np.asarray(mesh_points, dtype=float).reshape(-1, 3),
        stride=stride,
        role="density",
    )
    return _finite_samples(sampled)


def _refine_long_edges(
    faces: np.ndarray,
    indices: list[np.ndarray],
    rho: list[float],
    columns: list[list[np.ndarray] | None],
    *,
    source: CubeData,
    density: CubeData,
    stride: int,
    cutoff: float,
) -> list[tuple[int, int, int]]:
    """Subdivide edges whose chord would miss the sampled cutoff.

    Axis-aligned trilinear density is linear across one voxel. A longer edge
    is split only when its endpoints disagree about ρ ≤ ρ_cut, or a sample
    at most one voxel apart is on the other side. Edges that stay on one
    side are left whole. Each split halves every edge of that triangle, so
    the later linear cutoff lands on a one-voxel segment. Companion arrays
    use the same midpoint parameter as the position.
    """
    if len(indices) == 0:
        return []
    stacked = np.vstack(indices)
    density_index = [
        np.asarray(row, dtype=float)
        for row in _other_index_at_mesh_indices(
            source,
            density,
            stacked,
            stride=stride,
            role="density",
        )
    ]
    pending = [tuple(int(vertex) for vertex in face) for face in faces]
    refined: list[tuple[int, int, int]] = []
    midpoints: dict[tuple[int, int], int] = {}
    edge_split: dict[tuple[int, int], bool] = {}
    limit = max(256, 32 * len(pending))

    def _needs_split(start: int, end: int) -> bool:
        key = (start, end) if start < end else (end, start)
        cached = edge_split.get(key)
        if cached is not None:
            return cached
        span = float(np.linalg.norm(density_index[end] - density_index[start]))
        if span <= _MAX_INDEX_SPAN:
            edge_split[key] = False
            return False
        start_inside = rho[start] <= cutoff
        if start_inside != (rho[end] <= cutoff):
            edge_split[key] = True
            return True
        steps = int(np.ceil(span - 1e-9))
        fractions = np.linspace(0.0, 1.0, steps + 1)[1:-1]
        if fractions.size == 0:
            edge_split[key] = False
            return False
        points = (1.0 - fractions)[:, None] * indices[start] + fractions[:, None] * indices[end]
        samples = _sample_density(source, density, points, stride=stride)
        disagree = bool(np.any((samples <= cutoff) != start_inside))
        edge_split[key] = disagree
        return disagree

    def _midpoint(start: int, end: int) -> int:
        key = (start, end) if start < end else (end, start)
        found = midpoints.get(key)
        if found is not None:
            return found
        mesh_index = 0.5 * (indices[start] + indices[end])
        sampled = _sample_density(source, density, mesh_index, stride=stride)
        rho.append(float(sampled[0]))
        indices.append(mesh_index)
        density_index.append(0.5 * (density_index[start] + density_index[end]))
        for column in columns:
            if column is None:
                continue
            column.append(0.5 * (np.asarray(column[start]) + np.asarray(column[end])))
        found = len(indices) - 1
        midpoints[key] = found
        return found

    def _centroid_disagrees(face: tuple[int, int, int]) -> bool:
        spans = [
            float(np.linalg.norm(density_index[face[(corner + 1) % 3]] - density_index[face[corner]]))
            for corner in range(3)
        ]
        if max(spans) <= _MAX_INDEX_SPAN:
            return False
        flags = [rho[vertex] <= cutoff for vertex in face]
        if flags[0] != flags[1] or flags[1] != flags[2]:
            return False
        centroid = (indices[face[0]] + indices[face[1]] + indices[face[2]]) / 3.0
        sampled = float(_sample_density(source, density, centroid, stride=stride)[0])
        return (sampled <= cutoff) != flags[0]

    while pending:
        face = pending.pop()
        split = any(_needs_split(face[corner], face[(corner + 1) % 3]) for corner in range(3))
        if not split and not _centroid_disagrees(face):
            refined.append(face)
            continue
        if limit <= 0:
            raise RuntimeError(
                "density mask could not resolve a long edge against the sampled field"
            )
        limit -= 1
        first, second, third = face
        mid_first = _midpoint(first, second)
        mid_second = _midpoint(second, third)
        mid_third = _midpoint(third, first)
        pending.append((first, mid_first, mid_third))
        pending.append((mid_first, second, mid_second))
        pending.append((mid_third, mid_second, third))
        pending.append((mid_first, mid_second, mid_third))
    return refined


def filter_mesh_by_density(
    source: CubeData,
    density: CubeData,
    mesh_indices: np.ndarray,
    triangles: np.ndarray,
    arrays: tuple[np.ndarray | None, ...],
    *,
    stride: int,
    cutoff: float,
) -> tuple[np.ndarray, tuple[Any, ...]] | None:
    """Clip triangles to vertices with ρ ≤ ``cutoff``.

    ``mesh_indices`` are marching-cubes coordinates on the strided field.
    Density is sampled in the shared Cartesian frame; the cubes' value
    arrays are not written. A triangle wholly above the cutoff is removed.
    A triangle that crosses ρ_cut is split where the sampled density field
    crosses ρ_cut. Edges longer than one density voxel are subdivided until
    that parameter follows the field, so a coarse marching-cubes edge is not
    cut on the chord between its endpoint samples. A same-side edge whose
    interior sample crosses ρ_cut is subdivided too. Each companion array
    uses the same parameter. Returns None when nothing remains.
    """
    rho = sample_cube_at_mesh_indices(
        source,
        density,
        mesh_indices,
        stride=stride,
        role="density",
    )
    if rho.size and not np.all(np.isfinite(rho)):
        count = int(np.count_nonzero(~np.isfinite(rho)))
        raise ValueError(
            "density cube produced "
            f"{count} nonfinite samples; the isosurface was not masked "
            "with a replacement cutoff"
        )
    faces = np.asarray(triangles)
    if faces.size == 0:
        return None
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError("isosurface triangles must have shape (M, 3)")
    vertex_count = int(rho.shape[0])
    columns: list[np.ndarray | None] = []
    for array in arrays:
        if array is None:
            columns.append(None)
            continue
        values = np.asarray(array)
        if len(values) != vertex_count:
            raise ValueError("density-mask arrays must align with mesh vertices")
        columns.append(values)
    index_rows = [np.asarray(row, dtype=float) for row in np.asarray(mesh_indices, dtype=float)]
    rho_rows = [float(value) for value in rho]
    column_rows: list[list[np.ndarray] | None] = []
    for column in columns:
        if column is None:
            column_rows.append(None)
            continue
        column_rows.append([np.asarray(row) for row in column])
    faces = np.asarray(
        _refine_long_edges(
            faces,
            index_rows,
            rho_rows,
            column_rows,
            source=source,
            density=density,
            stride=stride,
            cutoff=float(cutoff),
        ),
        dtype=np.int64,
    )
    rho = np.asarray(rho_rows, dtype=float)
    columns = []
    for column in column_rows:
        columns.append(None if column is None else np.asarray(column))
    inside = rho <= cutoff
    clipped = []
    for face in faces:
        clipped.extend(
            _clip_tokens((int(face[0]), int(face[1]), int(face[2])), inside)
        )
    if not clipped:
        return None
    order: list[tuple] = []
    index_of: dict[tuple, int] = {}

    def _add(token: tuple) -> int:
        found = index_of.get(token)
        if found is None:
            found = len(order)
            index_of[token] = found
            order.append(token)
        return found

    remapped = np.asarray([[_add(token) for token in face] for face in clipped], dtype=np.int64)
    compacted: list[Any] = []
    for column in columns:
        if column is None:
            compacted.append(None)
            continue
        rows = []
        for token in order:
            if token[0] == "v":
                rows.append(np.asarray(column[token[1]]))
                continue
            start = int(token[1])
            end = int(token[2])
            span = float(rho[end] - rho[start])
            weight = 0.0 if span == 0.0 else (float(cutoff) - float(rho[start])) / span
            weight = float(min(1.0, max(0.0, weight)))
            rows.append((1.0 - weight) * np.asarray(column[start]) + weight * np.asarray(column[end]))
        compacted.append(np.asarray(rows))
    return remapped, tuple(compacted)


__all__ = [
    "ELECTRON_DENSITY_AU_PER_ANGSTROM3",
    "filter_mesh_by_density",
    "resolve_density_mask",
]
