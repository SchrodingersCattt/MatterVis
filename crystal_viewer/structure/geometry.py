from __future__ import annotations

import gemmi
import numpy as np

# Re-export canonical domain-neutral math primitives from crystal_viewer.math
from ..math.pbc import bond_vector_mic, nearest_lattice_shift_frac
from ..math.rotation import view_rotation, view_vec_to_elev_azim

# ── Orthogonalisation matrix ────────────────────────────────────────────────
def ortho_matrix(cell):
    a, b, c = cell.a, cell.b, cell.c
    al = np.radians(cell.alpha); be = np.radians(cell.beta); ga = np.radians(cell.gamma)
    cos_al, cos_be, cos_ga = np.cos(al), np.cos(be), np.cos(ga)
    sin_ga = np.sin(ga); vol = cell.volume
    M = np.array([
        [a, b*cos_ga, c*cos_be],
        [0, b*sin_ga, c*(cos_al - cos_be*cos_ga)/sin_ga],
        [0, 0,        vol/(a*b*sin_ga)]
    ])
    N = M / np.array([a, b, c])
    return M, N


def _wrap_frac01(frac):
    frac = np.array(frac, dtype=float)
    return frac - np.floor(frac)


def _nearest_pbc_cart(ref_cart, pos_cart, cell):
    ref = gemmi.Position(float(ref_cart[0]), float(ref_cart[1]), float(ref_cart[2]))
    pos = gemmi.Position(float(pos_cart[0]), float(pos_cart[1]), float(pos_cart[2]))
    nearest = cell.find_nearest_pbc_position(ref, pos, 0)
    return np.array([nearest.x, nearest.y, nearest.z], dtype=float)


__all__ = [name for name in globals() if not name.startswith("__")]
