from __future__ import annotations

import gemmi
import numpy as np

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
    delta_frac = np.array(aj['frac'], dtype=float) - np.array(ai['frac'], dtype=float)
    shift = nearest_lattice_shift_frac(delta_frac, M, search_radius=search_radius)
    delta_frac_mic = delta_frac - shift
    delta_cart = M @ delta_frac_mic
    return delta_cart, shift

def _nearest_pbc_cart(ref_cart, pos_cart, cell):
    ref = gemmi.Position(float(ref_cart[0]), float(ref_cart[1]), float(ref_cart[2]))
    pos = gemmi.Position(float(pos_cart[0]), float(pos_cart[1]), float(pos_cart[2]))
    nearest = cell.find_nearest_pbc_position(ref, pos, 0)
    return np.array([nearest.x, nearest.y, nearest.z], dtype=float)

# ── View rotation ───────────────────────────────────────────────────────────
def view_rotation(view_vec, up_vec=None):
    z = np.array(view_vec, dtype=float); z /= np.linalg.norm(z)
    if up_vec is None:
        up = np.array([0.,1.,0.]) if abs(z[1]) < 0.9 else np.array([0.,0.,1.])
    else:
        up = np.array(up_vec, dtype=float)
    x = np.cross(up, z)
    if np.linalg.norm(x) < 1e-6:
        up = np.array([1.,0.,0.]); x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x); y /= np.linalg.norm(y)
    return np.array([x, y, z])

# ── Convert view-direction vector to Axes3D elev/azim ───────────────────────
def view_vec_to_elev_azim(view_vec):
    """
    Convert a 3D Cartesian view direction vector to matplotlib Axes3D
    elevation and azimuth angles (degrees).
    view_vec points FROM the scene TOWARD the viewer.
    """
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    # elev: angle above xy-plane
    elev = np.degrees(np.arcsin(np.clip(v[2], -1, 1)))
    # azim: angle in xy-plane from x-axis
    azim = np.degrees(np.arctan2(v[1], v[0]))
    return elev, azim


__all__ = [name for name in globals() if not name.startswith("__")]
