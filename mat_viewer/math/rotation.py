from __future__ import annotations

import numpy as np


AXIS_VIEW_KEYS = ("a", "b", "c", "a*", "b*", "c*")


def normalize_vector(vector: np.ndarray, *, name: str = "vector") -> np.ndarray:
    """Return a normalized 3D vector or raise a clear validation error."""
    result = np.asarray(vector, dtype=float)
    if result.shape != (3,) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must be a finite three-dimensional vector")
    norm = float(np.linalg.norm(result))
    if norm < 1e-12:
        raise ValueError(f"{name} must be non-zero")
    return result / norm


def rotate_vector(vector: np.ndarray, axis: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rotate a vector about ``axis`` using Rodrigues' formula."""
    vec = np.asarray(vector, dtype=float)
    if vec.shape != (3,) or not np.all(np.isfinite(vec)):
        raise ValueError("vector must be a finite three-dimensional vector")
    if not np.isfinite(angle_deg):
        raise ValueError("angle_deg must be finite")
    if abs(angle_deg) < 1e-12:
        return vec.copy()
    unit_axis = normalize_vector(axis, name="axis")
    theta = np.deg2rad(float(angle_deg))
    return (
        vec * np.cos(theta)
        + np.cross(unit_axis, vec) * np.sin(theta)
        + unit_axis * np.dot(unit_axis, vec) * (1.0 - np.cos(theta))
    )


def orthogonalise_up(view_vec: np.ndarray, up_pick: np.ndarray) -> np.ndarray:
    """Return an up vector perpendicular to ``view_vec``.

    The fallback preserves a deterministic, right-handed camera basis when
    ``up_pick`` is parallel to the requested view direction.
    """
    forward = normalize_vector(view_vec, name="view_vec")
    up = np.asarray(up_pick, dtype=float)
    if up.shape != (3,) or not np.all(np.isfinite(up)):
        raise ValueError("up_pick must be a finite three-dimensional vector")
    projected = up - float(np.dot(up, forward)) * forward
    if float(np.linalg.norm(projected)) < 1e-9:
        for fallback in (
            np.array([0.0, 0.0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
        ):
            projected = fallback - float(np.dot(fallback, forward)) * forward
            if float(np.linalg.norm(projected)) >= 1e-9:
                break
    return normalize_vector(projected, name="orthogonalised up vector")


def orthonormal_camera_basis(
    forward: np.ndarray,
    up_hint: np.ndarray,
) -> np.ndarray:
    """Build a right-handed ``[right, up, forward]`` camera basis."""
    forward_unit = normalize_vector(forward, name="forward")
    up_unit = orthogonalise_up(forward_unit, up_hint)
    right = normalize_vector(np.cross(up_unit, forward_unit), name="right")
    up = normalize_vector(np.cross(forward_unit, right), name="up")
    return np.array([right, up, forward_unit])


def lattice_axes(matrix: np.ndarray) -> dict[str, np.ndarray]:
    """Return unit real and reciprocal lattice directions.

    MatterVis stores real lattice vectors in the rows of ``matrix`` and uses
    row-vector fractional coordinates: ``cart = frac @ matrix``. Reciprocal
    directions therefore occupy the columns of ``matrix^-1``.
    """
    lattice = np.asarray(matrix, dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        raise ValueError("lattice matrix must be a finite 3x3 matrix")
    try:
        reciprocal = np.linalg.inv(lattice)
    except np.linalg.LinAlgError as exc:
        raise ValueError(
            "lattice matrix is singular; cannot build reciprocal axes"
        ) from exc

    return {
        key: normalize_vector(vector, name=f"lattice axis {key}")
        for key, vector in (
            ("a", lattice[0]),
            ("b", lattice[1]),
            ("c", lattice[2]),
            ("a*", reciprocal[:, 0]),
            ("b*", reciprocal[:, 1]),
            ("c*", reciprocal[:, 2]),
        )
    }


def largest_face_camera_axis(matrix: np.ndarray) -> str:
    """Return the reciprocal normal of the largest lattice face.

    Row-vector lattices define the faces normal to a*, b*, and c* as
    b cross c, c cross a, and a cross b respectively. Ties prefer c*
    to preserve the conventional view for cubic cells.
    """
    lattice = np.asarray(matrix, dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        raise ValueError("lattice matrix must be a finite 3x3 matrix")
    candidates = (
        ("c*", float(np.linalg.norm(np.cross(lattice[0], lattice[1])))),
        ("b*", float(np.linalg.norm(np.cross(lattice[2], lattice[0])))),
        ("a*", float(np.linalg.norm(np.cross(lattice[1], lattice[2])))),
    )
    axis, area = max(candidates, key=lambda item: item[1])
    if area < 1e-12:
        raise ValueError("lattice matrix is degenerate; cannot select a face")
    return axis


def axis_camera_basis(matrix: np.ndarray, axis: str) -> np.ndarray:
    """Return a VESTA-style camera basis aligned to a lattice axis."""
    key = str(axis).strip().lower().replace(" ", "")
    aliases = {
        "astar": "a*",
        "a-star": "a*",
        "areciprocal": "a*",
        "a_reciprocal": "a*",
        "bstar": "b*",
        "b-star": "b*",
        "breciprocal": "b*",
        "b_reciprocal": "b*",
        "cstar": "c*",
        "c-star": "c*",
        "creciprocal": "c*",
        "c_reciprocal": "c*",
    }
    key = aliases.get(key, key)
    if key not in AXIS_VIEW_KEYS:
        raise ValueError(f"unknown axis: {axis!r}; pick one of {AXIS_VIEW_KEYS}")

    axes = lattice_axes(matrix)
    up_key = {"a": "c", "b": "c", "c": "b", "a*": "c*", "b*": "c*", "c*": "b*"}[key]
    return orthonormal_camera_basis(axes[key], axes[up_key])


def view_rotation(view_vec, up_vec=None):
    z = normalize_vector(view_vec, name="view_vec")
    if up_vec is None:
        up = np.array([0.0, 1.0, 0.0]) if abs(z[1]) < 0.9 else np.array([0.0, 0.0, 1.0])
    else:
        up = np.array(up_vec, dtype=float)
    return orthonormal_camera_basis(z, up)


def view_vec_to_elev_azim(view_vec):
    """Convert a Cartesian view-direction vector to Axes3D elev/azim."""
    v = normalize_vector(view_vec, name="view_vec")
    elev = np.degrees(np.arcsin(np.clip(v[2], -1, 1)))
    azim = np.degrees(np.arctan2(v[1], v[0]))
    return elev, azim


__all__ = [
    "AXIS_VIEW_KEYS",
    "axis_camera_basis",
    "lattice_axes",
    "largest_face_camera_axis",
    "normalize_vector",
    "orthogonalise_up",
    "orthonormal_camera_basis",
    "rotate_vector",
    "view_rotation",
    "view_vec_to_elev_azim",
]
