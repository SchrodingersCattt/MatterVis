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

from ..config import DEFAULT_STYLE

HYDROGEN_RADIUS_BOND_FACTOR = 1.5
# Thin coordination stroke for an ionic lattice ball-and-stick figure when the
# caller left the bond radius at the molecular default.  The viewer state
# builder uses the same value for inorganic lattice defaults.
INORGANIC_DEFAULT_BOND_RADIUS = 0.12
# Molecular ball-and-stick compacts spheres so the stick stays readable.
# Binary ionic lattices skip this factor and stay at the ball-style radius.
MOLECULAR_BALL_STICK_RADIUS_SCALE = 0.46
# Ionic coordination spheres must read as balls on sticks, including elements
# whose display radius would otherwise sit inside the cylinder.
IONIC_BALL_STICK_ATOM_TO_BOND_RATIO = 1.5


def coordination_stick_radius(
    bond_radius: float,
    *,
    representation: str,
    ionic_solid: bool,
) -> float:
    """Return the cylinder radius for one representation.

    Binary ionic ball-and-stick uses the viewer lattice stroke when the
    caller left the molecular default in place.  An explicit radius is kept.
    """
    radius = float(bond_radius)
    mode = str(representation).lower().replace("-", "_")
    if (
        ionic_solid
        and mode == "ball_stick"
        and radius == float(DEFAULT_STYLE["bond_radius"])
    ):
        return float(INORGANIC_DEFAULT_BOND_RADIUS)
    return radius


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
    lattice_coordination: bool = False,
) -> float:
    """Return one backend-neutral rendered atom radius.

    Non-hydrogen caller radii are preserved.  H/D receives the display floor
    after the representation's compact ball-stick scale is applied.  A binary
    ionic lattice drawn as ball-and-stick uses the ball radius, then a floor
    above the stick, so coordination spheres are not swallowed.
    """
    value = float(atom_radius)
    scale = float(atom_scale)
    if value < 0.0 or scale <= 0.0:
        raise ValueError("atom_radius and atom_scale must be positive")
    mode = str(representation).lower().replace("-", "_")
    use_ball_radius = mode in {"ball", "space_filling"} or (
        lattice_coordination and mode == "ball_stick"
    )
    base = value if use_ball_radius else value * MOLECULAR_BALL_STICK_RADIUS_SCALE
    rendered = base * scale
    if (
        lattice_coordination
        and mode == "ball_stick"
        and bond_radius is not None
        and float(bond_radius) > 0.0
    ):
        rendered = max(
            rendered,
            IONIC_BALL_STICK_ATOM_TO_BOND_RATIO * float(bond_radius),
        )
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
    "INORGANIC_DEFAULT_BOND_RADIUS",
    "coordination_stick_radius",
    "IONIC_BALL_STICK_ATOM_TO_BOND_RATIO",
    "MOLECULAR_BALL_STICK_RADIUS_SCALE",
    "atom_visual_radius",
    "clamp_displacement_radius",
    "hydrogen_radius_floor",
]
