"""Textual depth bands and display-only layer slicing.

Lattice slices use fractional coordinates already carried by the IR; camera
depth slices use Angstrom along the viewing direction. No chemistry is inferred.
"""

from __future__ import annotations

import numpy as np

DEPTH_EPSILON = 1e-9
DEPTH_MARKERS = ("¹", "²", "³")  # front, middle, back
BOND_DASH_PERIOD = 4
BOND_DASH_LENGTH = 2
CROSSING_GAP = 1
MAX_PARALLAX_FRAMES = 12


def depth_band(value: float, low: float, high: float) -> int:
    """Return front=0, middle=1, back=2; a flat scene is middle."""
    if high - low < DEPTH_EPSILON:
        return 1
    return min(2, max(0, 2 - int(3 * (value - low) / (high - low))))


def depth_marker(band: int, charset: str) -> str:
    return f"^{band + 1}" if charset == "ascii7" else DEPTH_MARKERS[band]


def layer_indices(crystal, camera, axis: str, width: float) -> np.ndarray:
    """Assign atoms to half-open display layers with a stable world origin."""
    if axis == "off" or not crystal.atoms:
        return np.zeros(len(crystal.atoms), dtype=int)
    if axis == "depth":
        values = crystal.cart_coords @ np.asarray(camera.rotation_matrix)[2]
    else:
        if crystal.lattice is None:
            raise ValueError("lattice slicing requires a crystal lattice")
        values = np.asarray([atom.frac["abc".index(axis)] for atom in crystal.atoms])
    return np.floor(values / width + DEPTH_EPSILON).astype(int)


def slice_distances(crystal, camera, axis: str, index: int, width: float) -> np.ndarray:
    if axis == "off":
        return np.zeros(len(crystal.atoms), dtype=int)
    return np.abs(layer_indices(crystal, camera, axis, width) - index)


def validate_slice(crystal, axis: str, index: int, width: float) -> None:
    if axis not in {"off", "depth", "a", "b", "c"}:
        raise ValueError("slice axis must be off, depth, a, b or c")
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError("slice index must be an integer")
    if not np.isfinite(width) or width <= 0:
        raise ValueError("slice width must be finite and positive")
    if axis in {"a", "b", "c"} and crystal.lattice is None:
        raise ValueError("lattice slicing requires a crystal lattice")
