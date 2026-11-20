"""Primitive-cell clipping planes shared by figure backends.

The domain is one primitive cell, not a repeated supercell. Planes are
the inward Cartesian half-spaces of the fractional faces 0 and 1. Skew
and left-handed cells take the outward direction from the reciprocal
lattice and store the inward normal. Boundaries are uncapped: a cut does
not invent a face. ``surface`` clips the isosurface only. ``strict`` also
clips atoms and bonds. ``none`` does not clip. Source coordinates are not
modified.

``cell_clip_planes`` is the geometry. A backend decides how to apply the
planes. Only the experimental PyVista cube figure does that today; other
backends do not claim the same clipping.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CELL_BOUNDARY_MODES = ("none", "surface", "strict")
_N_CELL_AXES = 3


@dataclass(frozen=True, slots=True)
class CellBoundaryPolicy:
    """Which primitive-cell planes a figure applies.

    ``surface`` clips the isosurface only. ``strict`` also clips atoms and
    bonds. ``none`` leaves geometry uncut. The mode does not change source
    coordinates, and the cuts stay uncapped.
    """

    mode: str

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str):
            raise TypeError("boundary_policy must be 'none', 'surface' or 'strict'")
        mode = self.mode.strip().lower()
        if mode not in CELL_BOUNDARY_MODES:
            raise ValueError(
                "boundary_policy must be 'none', 'surface' or 'strict'"
            )
        object.__setattr__(self, "mode", mode)


def cell_clip_planes(
    origin,
    vectors,
) -> tuple[tuple[np.ndarray, np.ndarray], ...]:
    """Return six inward half-spaces of the primitive cell.

    ``vectors`` are the three lattice rows. Each item is
    ``(point, inward_normal)``. The ``f = 0`` plane passes through
    ``origin``. The ``f = 1`` plane passes through ``origin`` plus that
    lattice row. ``inv(vectors)[:, axis]`` is the outward normal of the
    ``f = 1`` face for skew and left-handed cells alike. The stored normal
    points into the cell. A point translated by one lattice vector falls
    outside at least one plane.
    """

    corner = np.asarray(origin, dtype=float)
    lattice = np.asarray(vectors, dtype=float)
    if corner.shape != (_N_CELL_AXES,) or lattice.shape != (_N_CELL_AXES, _N_CELL_AXES):
        raise ValueError("cell origin must have shape (3,) and vectors shape (3, 3)")
    if not np.all(np.isfinite(corner)) or not np.all(np.isfinite(lattice)):
        raise ValueError("cell origin and vectors must be finite")
    try:
        inverse = np.linalg.inv(lattice)
    except np.linalg.LinAlgError as exc:
        raise ValueError("cell vectors must be linearly independent") from exc
    planes: list[tuple[np.ndarray, np.ndarray]] = []
    for axis in range(_N_CELL_AXES):
        # Reciprocal column: outward normal of the fractional face f = 1.
        outward = np.array(inverse[:, axis], dtype=float, copy=True)
        length = float(np.linalg.norm(outward))
        if length == 0.0 or not np.isfinite(length):
            raise ValueError("cell vectors must be linearly independent")
        outward /= length
        planes.append((corner.copy(), outward.copy()))
        planes.append(((corner + lattice[axis]).copy(), -outward))
    return tuple(planes)


__all__ = ["CELL_BOUNDARY_MODES", "CellBoundaryPolicy", "cell_clip_planes"]
