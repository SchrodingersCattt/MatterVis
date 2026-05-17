from __future__ import annotations

import numpy as np


def view_rotation(view_vec, up_vec=None):
    z = np.array(view_vec, dtype=float)
    z /= np.linalg.norm(z)
    if up_vec is None:
        up = np.array([0.0, 1.0, 0.0]) if abs(z[1]) < 0.9 else np.array([0.0, 0.0, 1.0])
    else:
        up = np.array(up_vec, dtype=float)
    x = np.cross(up, z)
    if np.linalg.norm(x) < 1e-6:
        up = np.array([1.0, 0.0, 0.0])
        x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    y /= np.linalg.norm(y)
    return np.array([x, y, z])


def view_vec_to_elev_azim(view_vec):
    """Convert a Cartesian view-direction vector to Axes3D elev/azim."""
    v = np.array(view_vec, dtype=float)
    v /= np.linalg.norm(v)
    elev = np.degrees(np.arcsin(np.clip(v[2], -1, 1)))
    azim = np.degrees(np.arctan2(v[1], v[0]))
    return elev, azim

__all__ = ["view_rotation", "view_vec_to_elev_azim"]
