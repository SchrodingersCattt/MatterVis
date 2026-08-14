"""Camera-aware material helpers for static publication figures."""

from __future__ import annotations

from typing import Any

import numpy as np
from matplotlib.colors import to_rgba


def _axis_camera_basis(ax: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Read the actual Matplotlib camera basis after limits and view are set."""
    ax.get_proj()
    return tuple(
        np.asarray(vector, dtype=float)
        for vector in (ax._view_u, ax._view_v, ax._view_w)
    )


def _camera_light(
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> np.ndarray:
    right, up, eye = basis
    light = eye - 0.34 * right + 0.38 * up
    return light / max(float(np.linalg.norm(light)), 1e-12)


def _polyhedron_facecolors(
    faces: list[np.ndarray],
    colors: list[Any],
    *,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    ambient: float,
    diffuse: float,
) -> np.ndarray:
    """Shade flat faces by orientation while preserving their input alpha."""
    if len(faces) != len(colors):
        raise ValueError("polyhedron faces and colors must have equal lengths")
    light = _camera_light(basis)
    shaded: list[np.ndarray] = []
    for face, color in zip(faces, colors):
        vertices = np.asarray(face, dtype=float)
        normal = np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0])
        normal /= max(float(np.linalg.norm(normal)), 1e-12)
        lambert = abs(float(np.dot(normal, light)))
        illumination = np.clip(
            float(ambient) + float(diffuse) * lambert,
            0.0,
            1.0,
        )
        rgba = np.asarray(to_rgba(color), dtype=float)
        rgba[:3] *= illumination
        shaded.append(rgba)
    return np.asarray(shaded)


def _sphere_facecolors(
    xyz: np.ndarray,
    center: np.ndarray,
    color: str,
    *,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    ambient: float,
    diffuse: float,
) -> np.ndarray:
    """Apply a bright ambient-diffuse material without Matplotlib's dark floor."""
    normals = xyz - center[None, None, :]
    lengths = np.linalg.norm(normals, axis=2, keepdims=True)
    normals /= np.maximum(lengths, 1e-12)
    light = _camera_light(basis)
    illumination = np.clip(
        float(ambient) + float(diffuse) * np.maximum(normals @ light, 0.0),
        0.0,
        1.0,
    )
    rgba = np.ones((*illumination.shape, 4), dtype=float)
    rgba[..., :3] = np.asarray(to_rgba(color)[:3]) * illumination[..., None]
    return rgba
