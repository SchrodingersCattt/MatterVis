"""Split Blinn–Phong and Fresnel rim response for the CPU rasterizer.

Specular math is separate from :class:`mat_viewer.render.lighting.LightingRig`.
The default light is :data:`mat_viewer.render.lighting.STUDIO_SOFT_DIRECTION`.
"""

from __future__ import annotations

import numpy as np

from ..lighting import STUDIO_SOFT_DIRECTION
from ..surface_material import MaterialSpec


def uses_split_composite(material: MaterialSpec | None, alpha: float) -> bool:
    """Translucent specular or Fresnel must not share one straight-alpha RGB."""

    if material is None:
        return False
    opacity = float(alpha)
    if not (0.0 < opacity < 1.0):
        return False
    return material.specular > 0.0 or material.modulates_opacity()


def abs_incidence(normals: np.ndarray, view: np.ndarray) -> np.ndarray:
    """``|n · v|``. A missing normal is treated as face-on."""

    normal = np.asarray(normals, dtype=float)
    direction = np.asarray(view, dtype=float)
    lengths = np.linalg.norm(normal, axis=-1, keepdims=True)
    unit = normal / np.maximum(lengths, 1.0e-12)
    if direction.ndim == 1:
        scale = max(float(np.linalg.norm(direction)), 1.0e-12)
        cosine = np.abs(unit @ (direction / scale))
    else:
        view_length = np.linalg.norm(direction, axis=-1, keepdims=True)
        cosine = np.abs(np.sum(unit * direction / np.maximum(view_length, 1.0e-12), axis=-1))
    return np.where(lengths[..., 0] > 1.0e-12, cosine, 1.0)


def camera_view(camera_points: np.ndarray, *, orthographic: bool) -> np.ndarray:
    """View direction toward the camera. Orthographic view is ``+Z``."""

    points = np.asarray(camera_points, dtype=float)
    if orthographic:
        view = np.zeros(points.shape, dtype=float)
        view[..., 2] = 1.0
        return view
    return -points


def modulated_alpha(
    base_alpha: float,
    incidence: np.ndarray,
    material: MaterialSpec,
) -> np.ndarray:
    """Scale object opacity by the Fresnel rim. Opaque objects stay opaque."""

    opacity = float(base_alpha)
    cosine = np.asarray(incidence, dtype=float)
    if not (0.0 < opacity < 1.0) or not material.modulates_opacity():
        return np.full(cosine.shape, opacity, dtype=float)
    weight = np.clip(1.0 - cosine, 0.0, 1.0) ** float(material.fresnel_power)
    scale = float(material.fresnel_center) + (
        float(material.fresnel_rim) - float(material.fresnel_center)
    ) * weight
    return np.clip(opacity * scale, 0.0, 1.0)


def _specular_highlight(
    unit: np.ndarray,
    light_vector: np.ndarray,
    view: np.ndarray | None,
    shininess: float,
) -> np.ndarray:
    """Blinn–Phong highlight. ``view is None`` is the orthographic ``+Z`` path."""

    if view is None:
        half_vector = light_vector + np.array((0.0, 0.0, 1.0), dtype=float)
        half_vector = half_vector / max(float(np.linalg.norm(half_vector)), 1.0e-12)
        return np.clip(unit @ half_vector, 0.0, 1.0) ** shininess
    direction = np.asarray(view, dtype=float)
    if direction.ndim == 1:
        direction = direction / max(float(np.linalg.norm(direction)), 1.0e-12)
        half_vector = light_vector + direction
        half_vector = half_vector / max(float(np.linalg.norm(half_vector)), 1.0e-12)
        return np.clip(unit @ half_vector, 0.0, 1.0) ** shininess
    view_length = np.linalg.norm(direction, axis=-1, keepdims=True)
    direction = direction / np.maximum(view_length, 1.0e-12)
    half_vector = light_vector + direction
    half_length = np.linalg.norm(half_vector, axis=-1, keepdims=True)
    half_vector = half_vector / np.maximum(half_length, 1.0e-12)
    return np.clip(np.sum(unit * half_vector, axis=-1), 0.0, 1.0) ** shininess


def split_blinn_phong(
    base_rgb: np.ndarray,
    normals: np.ndarray,
    material: MaterialSpec,
    *,
    fresnel_weight: np.ndarray | None = None,
    light: np.ndarray | None = None,
    two_sided: bool = True,
    view: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return diffuse/ambient RGB and reflected specular RGB separately.

    Their sum matches :func:`shade_blinn_phong` when ``fresnel_weight`` and
    ``view`` are omitted. ``view`` is the direction toward the camera:
    orthographic ``+Z``, or ``-normalize(p_camera)`` in perspective.
    """

    base = np.asarray(base_rgb, dtype=float)
    normal = np.asarray(normals, dtype=float)
    if light is None:
        direction = STUDIO_SOFT_DIRECTION
    else:
        direction = light
    light_vector = np.asarray(direction, dtype=float)
    light_vector = light_vector / max(float(np.linalg.norm(light_vector)), 1.0e-12)
    lengths = np.linalg.norm(normal, axis=-1, keepdims=True)
    unit = normal / np.maximum(lengths, 1.0e-12)
    unit = np.where(lengths > 1.0e-12, unit, 0.0)
    ndotl = unit @ light_vector
    if two_sided:
        ndotl = np.abs(ndotl)
    else:
        ndotl = np.clip(ndotl, 0.0, 1.0)
    diffuse = base * (float(material.ambient) + float(material.diffuse) * ndotl[..., None])
    if fresnel_weight is not None and material.fresnel_albedo > 0.0:
        # Lambert darkens where the normal is edge-on to the key light.
        # Blend toward the base color so a glass silhouette stays saturated.
        weight = np.asarray(fresnel_weight, dtype=float)[..., None]
        diffuse = diffuse * (1.0 - weight) + base * float(material.fresnel_albedo) * weight
    specular = np.zeros(diffuse.shape, dtype=float)
    coeff = float(material.specular)
    if fresnel_weight is not None and material.fresnel_specular > 0.0:
        coeff = coeff + float(material.fresnel_specular) * np.asarray(fresnel_weight, dtype=float)
    if material.specular_tint != "none" and (
        material.specular > 0.0 or material.fresnel_specular > 0.0
    ):
        highlight = _specular_highlight(
            unit, light_vector, view, float(material.shininess)
        )
        if material.specular_tint == "base":
            specular_rgb = base
        elif material.specular_color is not None:
            specular_rgb = np.asarray(material.specular_color, dtype=float)
        else:
            specular_rgb = np.ones(3, dtype=float)
        specular = highlight[..., None] * np.asarray(coeff, dtype=float)[..., None] * specular_rgb
    return diffuse, specular


def analytic_response(
    base_rgb: np.ndarray,
    normals: np.ndarray,
    material: MaterialSpec,
    view: np.ndarray,
    base_alpha: float,
    *,
    two_sided: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Diffuse, specular, and Fresnel alpha for an analytic normal field."""

    return evaluate_surface(
        base_rgb,
        normals,
        view,
        material,
        base_alpha=base_alpha,
        two_sided=two_sided,
        view_is_direction=True,
    )


def mesh_response_attributes(
    base_rgb: np.ndarray,
    normals: np.ndarray,
    camera_points: np.ndarray,
    material: MaterialSpec,
    *,
    orthographic: bool,
    base_alpha: float,
) -> np.ndarray:
    """Per-vertex diffuse RGB, specular RGB, and effective alpha. Shape ``(N, 7)``."""

    diffuse, specular, alpha = evaluate_surface(
        base_rgb,
        normals,
        camera_points,
        material,
        orthographic=orthographic,
        base_alpha=base_alpha,
    )
    return np.concatenate([diffuse, specular, alpha[:, None]], axis=-1)


def fresnel_weight_for(incidence: np.ndarray, material: MaterialSpec) -> np.ndarray | None:
    """Grazing weight used by rim color and rim specular. ``None`` leaves both off."""

    if material.fresnel_power <= 0.0:
        return None
    if material.fresnel_specular <= 0.0 and material.fresnel_albedo <= 0.0:
        return None
    return np.clip(1.0 - incidence, 0.0, 1.0) ** float(material.fresnel_power)


def evaluate_surface(
    base_rgb: np.ndarray,
    normals: np.ndarray,
    camera_points: np.ndarray,
    material: MaterialSpec,
    *,
    orthographic: bool = True,
    base_alpha: float,
    two_sided: bool = True,
    view_is_direction: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-sample diffuse, specular, and Fresnel alpha.

    ``camera_points`` are camera-space positions, unless
    ``view_is_direction`` is set and they are already view vectors.
    """

    view = (
        np.asarray(camera_points, dtype=float)
        if view_is_direction
        else camera_view(camera_points, orthographic=orthographic)
    )
    incidence = abs_incidence(normals, view)
    diffuse, specular = split_blinn_phong(
        base_rgb,
        normals,
        material,
        fresnel_weight=fresnel_weight_for(incidence, material),
        view=view,
        two_sided=two_sided,
    )
    return diffuse, specular, modulated_alpha(base_alpha, incidence, material)


def phong_vertex_attributes(
    normals: np.ndarray,
    base_rgb: np.ndarray,
    camera_points: np.ndarray,
) -> np.ndarray:
    """Camera normal, base RGB, and camera position. Shape ``(N, 9)``."""

    points = np.asarray(camera_points, dtype=float)
    base = np.asarray(base_rgb, dtype=float)
    if base.ndim == 1:
        base = np.broadcast_to(base, points.shape).copy()
    return np.concatenate(
        [np.asarray(normals, dtype=float), base, points], axis=1
    )


__all__ = [
    "abs_incidence",
    "analytic_response",
    "camera_view",
    "evaluate_surface",
    "fresnel_weight_for",
    "mesh_response_attributes",
    "modulated_alpha",
    "phong_vertex_attributes",
    "split_blinn_phong",
    "uses_split_composite",
]
