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
# Coordination ball-and-stick uses the same cylinders as a covalent stick.
# The compact 0.46 ball-stick scale sits on the display radius and leaves
# the sphere no larger than the cylinder. A coordination endpoint stays at
# least this many bond-radii across so the contact reads as ball-and-stick.
COORDINATION_RADIUS_BOND_FACTOR = 2.0


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


def rendered_atom_radius(
    *,
    element: str,
    stored_radius: float,
    representation: str,
    atom_scale: float = 1.0,
    bond_radius: float | None = None,
    min_atom_radius: float | None = None,
    coordination: bool = False,
) -> float:
    """Return the sphere radius for one representation.

    ``vdw`` uses MolCrysKit's van der Waals table and ignores
    ``stored_radius``. ``ball`` and ``space_filling`` keep the compact
    display radius, with the existing 0.2 Å floor. Other styles keep the
    0.46 ball-and-stick scale and the 0.16 Å floor.
    """
    mode = str(representation).lower().replace("-", "_")
    if mode == "vdw":
        from ..config import vdw_radius

        value = float(vdw_radius(element))
        coordination = False
    else:
        value = float(stored_radius)
    rendered = atom_visual_radius(
        value,
        element=element,
        representation=representation,
        atom_scale=atom_scale,
        bond_radius=bond_radius,
        min_atom_radius=min_atom_radius,
        coordination=coordination and mode == "ball_stick",
    )
    if mode == "vdw":
        return rendered
    if mode in {"space_filling", "ball"}:
        return max(rendered, 0.2)
    return max(rendered, 0.16)


def atom_visual_radius(
    atom_radius: float,
    *,
    element: str,
    representation: str,
    atom_scale: float = 1.0,
    style: Mapping[str, Any] | None = None,
    bond_radius: float | None = None,
    min_atom_radius: float | None = None,
    coordination: bool = False,
) -> float:
    """Return one backend-neutral rendered atom radius.

    Non-hydrogen caller radii are preserved.  H/D receives the display floor
    after the representation's compact ball-stick scale is applied.
    ``coordination`` is set by the caller for an endpoint of a MolCrysKit
    coordination edge when the active style strokes sticks. The sphere is
    then at least ``COORDINATION_RADIUS_BOND_FACTOR`` times the bond
    cylinder, so the compact ball-stick scale cannot swallow the atom.
    Ball and space-filling callers leave the flag false.

    ``vdw`` is not that compact scale. The caller passes one tabulated van
    der Waals radius from MolCrysKit. This function multiplies it by
    ``atom_scale`` only: no 0.46 factor, no hydrogen display floor, and no
    substitution of the compact display table.
    """
    value = float(atom_radius)
    scale = float(atom_scale)
    if value < 0.0 or scale <= 0.0:
        raise ValueError("atom_radius and atom_scale must be positive")
    mode = str(representation).lower().replace("-", "_")
    if mode == "vdw":
        return value * scale
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
    if coordination:
        if bond_radius is None and style is not None:
            bond_radius = style.get("bond_radius", 0.15)
        cylinder = float(0.15 if bond_radius is None else bond_radius)
        if cylinder <= 0.0 or not np.isfinite(cylinder):
            raise ValueError("bond_radius must be positive")
        rendered = max(rendered, COORDINATION_RADIUS_BOND_FACTOR * cylinder)
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


def display_sphere_radius(
    atom: Mapping[str, Any],
    style: Mapping[str, Any] | None = None,
    *,
    minimum_stored_radius: float | None = None,
) -> float:
    """Radius of the sphere drawn for ``style``.

    ``vdw`` reads MolCrysKit once inside :func:`rendered_atom_radius` and
    multiplies that tabulated value by ``atom_scale`` only. Other styles
    pass the stored display radius as representation ``ball``, so a
    ball-and-stick 0.46 factor already stored on the atom is not applied
    again. A ball-and-stick coordination endpoint still clears the bond
    cylinder.
    """
    values = style or {}
    representation = str(values.get("style", "ball")).lower().replace("-", "_")
    element = str(
        atom.get("elem") or atom.get("symbol") or atom.get("element") or "C"
    )
    atom_scale = float(values.get("atom_scale", 1.0))
    bond_radius = values.get("bond_radius")
    min_atom_radius = values.get("min_atom_radius")
    if representation == "vdw":
        return rendered_atom_radius(
            element=element,
            stored_radius=0.0,
            representation="vdw",
            atom_scale=atom_scale,
            bond_radius=None if bond_radius is None else float(bond_radius),
            min_atom_radius=(
                None if min_atom_radius is None else float(min_atom_radius)
            ),
            coordination=False,
        )
    stored = atom.get("atom_radius", atom.get("radius", 0.18))
    value = float(stored)
    if minimum_stored_radius is not None:
        value = max(value, float(minimum_stored_radius))
    return atom_visual_radius(
        value,
        element=element,
        representation="ball",
        atom_scale=atom_scale,
        style=values,
        bond_radius=bond_radius,
        min_atom_radius=min_atom_radius,
        coordination=(
            representation == "ball_stick" and bool(atom.get("_coordination_site"))
        ),
    )


def display_atom_radii(
    atoms,
    style: Mapping[str, Any] | None = None,
    *,
    minimum_stored_radius: float | None = 0.05,
) -> np.ndarray:
    """Radii used by traces' callers and by viewport fitting."""
    return np.asarray(
        [
            display_sphere_radius(
                atom,
                style,
                minimum_stored_radius=minimum_stored_radius,
            )
            for atom in atoms
        ],
        dtype=float,
    )


def union_representation_bounds(
    mins,
    maxs,
    atoms,
    *,
    representation: str = "ball_stick",
    atom_scale: float = 1.0,
    bond_radius: float = 0.15,
    min_atom_radius: float | None = None,
):
    """Grow an axis-aligned box so each drawn sphere lies inside it.

    Bounds built for the viewer already contain the compact display radius.
    ``ball`` and ``ball_stick`` therefore stay put. ``vdw`` extends the box
    to the tabulated van der Waals sphere.
    """
    low = np.asarray(mins, dtype=float).copy()
    high = np.asarray(maxs, dtype=float).copy()
    if low.shape != (3,) or high.shape != (3,):
        return low, high
    for atom in atoms or ():
        if not isinstance(atom, dict) or atom.get("_render_visible", True) is False:
            continue
        cart_value = atom.get("cart", atom.get("cartesian_position_A"))
        if cart_value is None:
            continue
        try:
            cart = np.asarray(cart_value, dtype=float)
            stored = float(atom.get("atom_radius", atom.get("radius", 0.18)))
        except (TypeError, ValueError):
            continue
        if cart.shape != (3,) or not np.all(np.isfinite(cart)):
            continue
        element = str(atom.get("elem") or atom.get("symbol") or atom.get("element") or "C")
        chosen = str(atom.get("_render_style") or representation or "ball_stick")
        radius = rendered_atom_radius(
            element=element,
            stored_radius=stored,
            representation=chosen,
            atom_scale=float(atom_scale),
            bond_radius=float(bond_radius),
            min_atom_radius=min_atom_radius,
            coordination=(
                chosen.lower().replace("-", "_") == "ball_stick"
                and bool(atom.get("_coordination_site"))
            ),
        )
        low = np.minimum(low, cart - radius)
        high = np.maximum(high, cart + radius)
    return low, high


def cover_axis_ranges(
    ranges,
    atoms,
    *,
    representation: str = "ball_stick",
    atom_scale: float = 1.0,
    bond_radius: float = 0.15,
    min_atom_radius: float | None = None,
) -> list[list[float]]:
    """Return axis ranges that contain the representation's spheres."""
    mins = [float(axis[0]) for axis in ranges]
    maxs = [float(axis[1]) for axis in ranges]
    low, high = union_representation_bounds(
        mins,
        maxs,
        atoms,
        representation=representation,
        atom_scale=atom_scale,
        bond_radius=bond_radius,
        min_atom_radius=min_atom_radius,
    )
    return [[float(low[index]), float(high[index])] for index in range(3)]


__all__ = [
    "COORDINATION_RADIUS_BOND_FACTOR",
    "HYDROGEN_RADIUS_BOND_FACTOR",
    "atom_visual_radius",
    "clamp_displacement_radius",
    "cover_axis_ranges",
    "display_atom_radii",
    "display_sphere_radius",
    "hydrogen_radius_floor",
    "rendered_atom_radius",
    "union_representation_bounds",
]
