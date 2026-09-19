"""Backend-neutral ORTEP displacement selection policies."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .geometry import fixed_isotropic_displacement


def displacement_for_atom(
    atom,
    *,
    element: str,
    hydrogen_radius: float | None,
    crystallographic_displacement: Callable[[object], np.ndarray | None],
) -> np.ndarray | None:
    if element == "H" and hydrogen_radius is not None:
        return fixed_isotropic_displacement(hydrogen_radius)
    return crystallographic_displacement(atom)