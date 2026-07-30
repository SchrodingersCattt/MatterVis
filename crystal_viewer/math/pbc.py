from __future__ import annotations

import numpy as np


def nearest_lattice_shift_frac(delta_frac, M, search_radius=1):
    delta_frac = np.array(delta_frac, dtype=float)
    best_shift = np.zeros(3)
    best_dist = np.inf
    for na in range(-search_radius, search_radius + 1):
        for nb in range(-search_radius, search_radius + 1):
            for nc in range(-search_radius, search_radius + 1):
                shift = np.array([na, nb, nc], dtype=float)
                dist = np.linalg.norm(M @ (delta_frac - shift))
                if dist < best_dist:
                    best_dist = dist
                    best_shift = shift
    return best_shift


def bond_vector_mic(ai, aj, M, search_radius=1):
    delta_frac = np.array(aj["frac"], dtype=float) - np.array(ai["frac"], dtype=float)
    shift = nearest_lattice_shift_frac(delta_frac, M, search_radius=search_radius)
    delta_frac_mic = delta_frac - shift
    delta_cart = M @ delta_frac_mic
    return delta_cart, shift


def nearest_image_vector_cart(delta_cart, M):
    """Return Cartesian MIC vector and applied image shift for row-vector cells.

    MatterVis terminal structures use ``cart = frac @ M``. ASE supplies the
    robust nearest-image search; the returned integer shift satisfies
    ``mic = (frac + shift) @ M``.
    """
    from ase.geometry import find_mic

    matrix = np.asarray(M, dtype=float)
    vector = np.asarray(delta_cart, dtype=float)
    mic, _ = find_mic(vector, cell=matrix, pbc=True)
    fractional = vector @ np.linalg.inv(matrix)
    mic_fractional = np.asarray(mic, dtype=float) @ np.linalg.inv(matrix)
    shift = np.rint(mic_fractional - fractional).astype(int)
    return np.asarray(mic, dtype=float), shift

__all__ = ["bond_vector_mic", "nearest_image_vector_cart", "nearest_lattice_shift_frac"]
