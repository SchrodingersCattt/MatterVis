"""Optional high-density ceiling for an interaction isosurface.

The mask is off unless the caller turns it on. It samples a density cube at
mesh vertices and drops triangles that enter ρ > ρ_cut. It does not rewrite
the field cube, the density cube, or a colour cube, and it does not choose
the cutoff.

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
    """Drop triangles with any vertex density above ``cutoff``.

    ``mesh_indices`` are marching-cubes coordinates on the strided field.
    Density is sampled in the shared Cartesian frame; the cubes' value
    arrays are not written. A triangle is kept when every vertex has
    ρ ≤ ρ_cut. Returns None when every triangle is above the ceiling.
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
    keep = np.all(rho[faces] <= cutoff, axis=1)
    kept = faces[keep]
    if kept.size == 0:
        return None
    used = np.unique(kept.ravel())
    remap = np.full(int(rho.shape[0]), -1, dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)
    compacted: list[Any] = []
    for array in arrays:
        if array is None:
            compacted.append(None)
            continue
        compacted.append(np.asarray(array)[used])
    return np.asarray(remap[kept], dtype=np.int64), tuple(compacted)


__all__ = [
    "ELECTRON_DENSITY_AU_PER_ANGSTROM3",
    "filter_mesh_by_density",
    "resolve_density_mask",
]
