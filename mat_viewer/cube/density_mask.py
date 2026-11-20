"""Optional high-density ceiling for an interaction isosurface.

The mask is off unless the caller turns it on. It samples a density cube at
mesh vertices and clips triangles to ρ ≤ ρ_cut. A mixed triangle is cut on
its edges where ρ = ρ_cut; companion arrays are linearly interpolated there.
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

from .color_field import sample_cube_at_mesh_indices
from .io import CubeData

# 1 a.u. of electron density ≈ 6.748 e/Å³. Documented for callers; not applied.
ELECTRON_DENSITY_AU_PER_ANGSTROM3 = 6.748


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
    A triangle that crosses ρ_cut is split at the linearly interpolated
    edge points, and each companion array is interpolated with the same
    weight. Returns None when nothing remains.
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
