"""Shared visual-radius policies for atom and bond renderers.

Chemistry supplies the element radius.  This module only applies a display
floor for hydrogen so a thin N-H/O-H bond cannot hide the atom marker.  The
floor follows the bond radius by default and can be overridden by the public
``min_atom_radius`` style/config field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

HYDROGEN_RADIUS_BOND_FACTOR = 1.5


def hydrogen_radius_floor(
    style: Mapping[str, Any] | None = None,
    *,
    bond_radius: float | None = None,
    min_atom_radius: float | None = None,
) -> float:
    """Return the effective H/D radius floor in Angstroms.

    ``min_atom_radius`` is an explicit caller override.  When omitted, the
    floor is ``1.5 * bond_radius``; this keeps the policy stable as the bond
    slider changes while retaining a small, easy-to-configure public knob.
    """
    values = style or {}
    if min_atom_radius is None:
        min_atom_radius = values.get("min_atom_radius")
    if min_atom_radius is not None:
        value = float(min_atom_radius)
        if value < 0.0:
            raise ValueError("min_atom_radius must be non-negative")
        return value
    if bond_radius is None:
        bond_radius = values.get("bond_radius", 0.15)
    value = float(bond_radius)
    if value <= 0.0:
        raise ValueError("bond_radius must be positive")
    return HYDROGEN_RADIUS_BOND_FACTOR * value


def atom_visual_radius(
    atom_radius: float,
    *,
    element: str,
    representation: str,
    atom_scale: float = 1.0,
    style: Mapping[str, Any] | None = None,
    bond_radius: float | None = None,
    min_atom_radius: float | None = None,
) -> float:
    """Return one backend-neutral rendered atom radius.

    Non-hydrogen caller radii are preserved.  H/D receives the display floor
    after the representation's compact ball-stick scale is applied.
    """
    value = float(atom_radius)
    scale = float(atom_scale)
    if value < 0.0 or scale <= 0.0:
        raise ValueError("atom_radius and atom_scale must be positive")
    mode = str(representation).lower().replace("-", "_")
    base = value if mode in {"ball", "space_filling"} else value * 0.46
    rendered = base * scale
    if str(element).strip().capitalize() in {"H", "D"}:
        rendered = max(
            rendered,
            hydrogen_radius_floor(
                style,
                bond_radius=bond_radius,
                min_atom_radius=min_atom_radius,
            ),
        )
    return rendered


def clamp_displacement_radius(
    displacement: Any,
    minimum_radius: float,
    *,
    probability: float = 0.5,
) -> np.ndarray:
    """Scale an ADP matrix only when its displayed ellipsoid is too small."""
    matrix = np.asarray(displacement, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValueError("displacement must be a finite 3x3 matrix")
    minimum = float(minimum_radius)
    if minimum <= 0.0:
        return 0.5 * (matrix + matrix.T)
    matrix = 0.5 * (matrix + matrix.T)
    eigenvalues = np.linalg.eigvalsh(matrix)
    if float(eigenvalues.min()) < -1e-10:
        raise ValueError("displacement matrix must be positive semidefinite")
    from .geometry import _chi_square_radius

    displayed_max = _chi_square_radius(probability) * float(
        np.sqrt(max(0.0, float(eigenvalues.max())))
    )
    if displayed_max < minimum and displayed_max > 0.0:
        factor = minimum / displayed_max
        matrix *= factor * factor
    elif displayed_max <= 0.0:
        matrix = np.eye(3) * (minimum / _chi_square_radius(probability)) ** 2
    return matrix


__all__ = [
    "HYDROGEN_RADIUS_BOND_FACTOR",
    "atom_visual_radius",
    "clamp_displacement_radius",
    "hydrogen_radius_floor",
]
