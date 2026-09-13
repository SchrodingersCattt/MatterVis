"""Aspect-correct terminal projection and atom hit-map primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .text import terminal_text

if TYPE_CHECKING:
    from .crystal_ir import CrystalIR


# Terminal character cells are approximately twice as tall as they are wide.
CHAR_ASPECT = 2.0


@dataclass
class Viewport:
    """Uniform-scale viewport mapping data coordinates to terminal cells."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    scale: float
    width: int
    height: int

    def to_grid(self, x: float, y: float) -> tuple[int, int]:
        """Map data coordinates to a top-origin ``(row, column)`` cell."""
        col = int((x - self.x_min) * self.scale)
        row = int((self.y_max - y) * self.scale / CHAR_ASPECT)
        return row, col

    def to_px(self, x: float, y: float) -> tuple[int, int]:
        """Map data coordinates to Braille subpixels."""
        px_x = int((x - self.x_min) * self.scale * 2)
        px_y = int((self.y_max - y) * self.scale / CHAR_ASPECT * 4)
        return px_x, px_y

    def in_bounds_grid(self, row: int, col: int) -> bool:
        return 0 <= row < self.height and 0 <= col < self.width


@dataclass(frozen=True)
class ProjectedAtomHit:
    """One visible atom retained from the exact terminal projection."""

    display_index: int
    atom_id: str
    display_copy_id: str
    label: str
    row: int
    col: int
    depth: float


def build_atom_hit_map(
    crystal: "CrystalIR",
    pts_2d: np.ndarray,
    depth: np.ndarray,
    viewport: Viewport,
    *,
    show_minor: bool,
) -> tuple[ProjectedAtomHit, ...]:
    """Retain visible atom screen positions for selection and mouse hits.

    The function consumes the same projection and effective viewport as the
    compositor. It does not infer connectivity or any chemical property.
    """
    hits: list[ProjectedAtomHit] = []
    count = min(len(crystal.atoms), len(pts_2d), len(depth))
    for index in range(count):
        atom = crystal.atoms[index]
        if not show_minor and atom.is_minor:
            continue
        row, col = viewport.to_grid(float(pts_2d[index][0]), float(pts_2d[index][1]))
        if not viewport.in_bounds_grid(row, col):
            continue
        hits.append(
            ProjectedAtomHit(
                display_index=index,
                atom_id=terminal_text(atom.atom_id),
                display_copy_id=terminal_text(atom.display_copy_id),
                label=terminal_text(atom.display_label),
                row=row,
                col=col,
                depth=float(depth[index]),
            )
        )
    return tuple(hits)


def _compute_viewport(
    pts_2d: np.ndarray,
    extra_pts: list[np.ndarray],
    width: int,
    height: int,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Viewport:
    """Compute an aspect-correct viewport for any positive zoom."""
    if zoom <= 0:
        raise ValueError("zoom must be greater than zero")
    all_arrays = [pts_2d] if len(pts_2d) > 0 else []
    all_arrays.extend(points for points in extra_pts if len(points) > 0)

    if all_arrays:
        combined = np.vstack(all_arrays)
        x_min, y_min = combined.min(axis=0)
        x_max, y_max = combined.max(axis=0)
    else:
        x_min = y_min = -1.0
        x_max = y_max = 1.0

    x_range = max(x_max - x_min, 0.01)
    y_range = max(y_max - y_min, 0.01)
    pad = 0.12
    x_min -= x_range * pad
    x_max += x_range * pad
    y_min -= y_range * pad
    y_max += y_range * pad

    return viewport_from_bounds(
        x_min,
        x_max,
        y_min,
        y_max,
        width,
        height,
        zoom=zoom,
        pan_x=pan_x,
        pan_y=pan_y,
    )


def viewport_from_bounds(
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    width: int,
    height: int,
    *,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Viewport:
    """Create an aspect-correct viewport from already fitted data bounds."""
    if zoom <= 0:
        raise ValueError("zoom must be greater than zero")
    width = max(int(width), 1)
    height = max(int(height), 1)
    x_range = max(float(x_max) - float(x_min), 0.01)
    y_range = max(float(y_max) - float(y_min), 0.01)

    cx = (x_min + x_max) / 2
    cy = (y_min + y_max) / 2
    x_range /= zoom
    y_range /= zoom
    x_min = cx - x_range / 2
    x_max = cx + x_range / 2
    y_min = cy - y_range / 2
    y_max = cy + y_range / 2

    if pan_x != 0.0 or pan_y != 0.0:
        x_min += pan_x
        x_max += pan_x
        y_min += pan_y
        y_max += pan_y

    scale_x = (width - 1) / x_range if x_range > 0 else 1.0
    scale_y = (height - 1) * CHAR_ASPECT / y_range if y_range > 0 else 1.0
    scale = min(scale_x, scale_y)

    return Viewport(
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        scale=scale,
        width=width,
        height=height,
    )


__all__ = [
    "CHAR_ASPECT",
    "ProjectedAtomHit",
    "Viewport",
    "_compute_viewport",
    "build_atom_hit_map",
    "viewport_from_bounds",
]
