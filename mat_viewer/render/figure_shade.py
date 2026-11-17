"""Isosurface shade coefficients shared by figure entries.

``FigureShade`` is the ambient, diffuse, specular, and shininess of one
isosurface. Callers pass this record. The four numbers are the same
coefficients as ``IsosurfaceMaterial`` when that API is present. This
module does not import that type, and it does not carry opacity or a
light scale.

The weights are independent. They are not forced to sum to one, and they
are not renamed for a particular renderer. A backend maps ``shininess``
onto its own highlight exponent.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

# Shared coefficient bounds. Weights may exceed 1; shininess is an exponent.
_MIN_COEFFICIENT = 0.0
_MIN_SHININESS = 1.0


def _finite(name: str, value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _nonnegative(name: str, value: float) -> float:
    number = _finite(name, value)
    if number < _MIN_COEFFICIENT:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _shininess(value: float) -> float:
    number = _finite("shininess", value)
    if number < _MIN_SHININESS:
        raise ValueError("shininess must be finite and at least 1")
    return number


@dataclass(frozen=True, slots=True)
class FigureShade:
    """Ambient, diffuse, specular, and shininess of one isosurface.

    Callers pass this record into a figure entry. These are the same four
    numbers as ``IsosurfaceMaterial`` when that API is present.
    ``shininess`` is the highlight exponent.
    """

    ambient: float
    diffuse: float
    specular: float
    shininess: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "ambient", _nonnegative("ambient", self.ambient))
        object.__setattr__(self, "diffuse", _nonnegative("diffuse", self.diffuse))
        object.__setattr__(self, "specular", _nonnegative("specular", self.specular))
        object.__setattr__(self, "shininess", _shininess(self.shininess))


__all__ = ["FigureShade"]
