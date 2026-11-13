"""CPU isosurface extraction for Cube inputs.

scikit-image is imported only when an isosurface is requested.  The returned
mesh dictionaries are backend-neutral and can be consumed by RenderPlan.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..render.isosurface_material import (
    IsosurfaceMaterial,
    resolve_isosurface_material,
)
from .color_field import isosurface_preparation_token, vertex_colors_for_surface
from .io import CubeData, default_isovalue


def cube_isosurface_meshes(
    cube: CubeData,
    *,
    isovalue: float | None = None,
    percentile: float = 98.5,
    stride: int = 1,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    opacity: float = 0.55,
    ambient: float | None = None,
    diffuse: float | None = None,
    specular: float | None = None,
    shininess: float | None = None,
    light_scale: float | None = None,
    material: str | IsosurfaceMaterial | None = None,
    color_cube: CubeData | None = None,
    color_range: tuple[float, float] | None = None,
    color_center: float | None = None,
    colormap: str = "bgr",
) -> list[dict[str, Any]]:
    """Extract positive/negative phases as world-space triangle meshes.

    ``color_cube`` is sampled at each vertex's physical position on the full
    color grid. ``stride`` coarsens the marching-cubes mesh only.
    """
    try:
        from skimage.measure import marching_cubes
    except ImportError as exc:  # pragma: no cover - exercised in minimal CI
        from ..capabilities import resolve_requirements

        install = resolve_requirements("cube").install_command
        raise ImportError(
            f"Cube isosurfaces require the 'cube' capability. Install with: {install}"
        ) from exc

    stride = max(1, int(stride))
    values = np.asarray(cube.values[::stride, ::stride, ::stride], dtype=float)
    iso = (
        float(isovalue)
        if isovalue is not None
        else default_isovalue(values, percentile=percentile)
    )
    if not np.isfinite(iso) or iso <= 0.0:
        raise ValueError("Cube isovalue must be finite and positive")
    surface_material = _surface_material(
        material,
        ambient=ambient,
        diffuse=diffuse,
        specular=specular,
        shininess=shininess,
        opacity=opacity,
        light_scale=light_scale,
    )
    basis = np.asarray(cube.axes, dtype=float) * stride
    inverse_normal = np.linalg.inv(basis).T
    meshes: list[dict[str, Any]] = []
    phases = (
        (iso, positive_color, "cube:positive"),
        (-iso, negative_color, "cube:negative"),
    )
    minimum = float(np.min(values))
    maximum = float(np.max(values))
    for level, color, semantic_id in phases:
        if not minimum < level < maximum:
            continue
        try:
            vertices, faces, normals, _ = marching_cubes(values, level=level)
        except (RuntimeError, ValueError):
            continue
        # Cube atom coordinates are normalized by cube_to_raw_atoms into the scene frame.
        # Keep CPU isosurfaces in that same frame when cube.origin is nonzero.
        world_vertices = vertices @ basis
        world_normals = normals @ inverse_normal
        lengths = np.linalg.norm(world_normals, axis=1)
        valid = lengths > 1.0e-12
        world_normals[valid] /= lengths[valid, None]
        mesh = {
            "id": semantic_id,
            "name": "+ orbital" if level > 0 else "- orbital",
            "vertices": world_vertices,
            "triangles": np.asarray(faces, dtype=np.int64),
            "normals": world_normals,
            "color": color,
            "opacity": surface_material.opacity,
            "metadata": {"material": surface_material.as_dict()},
        }
        if color_cube is not None:
            mesh["vertex_colors"] = vertex_colors_for_surface(
                cube,
                color_cube,
                vertices,
                stride=stride,
                color_range=color_range,
                color_center=color_center,
                colormap=colormap,
            )
        meshes.append(mesh)
    if not meshes:
        raise ValueError(f"Cube has no surface crossing isovalue {iso:g}")
    return meshes


def _surface_material(
    material: str | IsosurfaceMaterial | None,
    *,
    ambient: float | None,
    diffuse: float | None,
    specular: float | None,
    shininess: float | None,
    opacity: float,
    light_scale: float | None,
) -> IsosurfaceMaterial:
    """Resolve one shared record, including an already built material."""
    if isinstance(material, IsosurfaceMaterial):
        return _material_instance(
            material,
            ambient=ambient,
            diffuse=diffuse,
            specular=specular,
            shininess=shininess,
            opacity=opacity,
            light_scale=light_scale,
        )
    return resolve_isosurface_material(
        None if material is None else str(material),
        ambient=ambient,
        diffuse=diffuse,
        specular=specular,
        shininess=shininess,
        opacity=opacity,
        light_scale=light_scale,
    )


def _material_instance(
    material: IsosurfaceMaterial,
    *,
    ambient: float | None,
    diffuse: float | None,
    specular: float | None,
    shininess: float | None,
    opacity: float,
    light_scale: float | None,
) -> IsosurfaceMaterial:
    if any(
        value is not None
        for value in (ambient, diffuse, specular, shininess, light_scale)
    ):
        raise ValueError(
            "Pass an isosurface material preset or the light coefficients, not both. "
            "Opacity can be combined with either."
        )
    if float(opacity) == material.opacity:
        return material
    return IsosurfaceMaterial(
        ambient=material.ambient,
        diffuse=material.diffuse,
        specular=material.specular,
        shininess=material.shininess,
        opacity=opacity,
        light_scale=material.light_scale,
    )


def ensure_cube_isosurfaces(
    source: Any,
    *,
    isovalue: float | None = None,
    opacity: float = 0.55,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    stride: int = 2,
    ambient: float | None = None,
    diffuse: float | None = None,
    specular: float | None = None,
    shininess: float | None = None,
    light_scale: float | None = None,
    material: str | IsosurfaceMaterial | None = None,
    color_cube: CubeData | None = None,
    color_range: tuple[float, float] | None = None,
    color_center: float | None = None,
    colormap: str = "bgr",
) -> Any:
    """Attach backend-neutral isosurfaces to every Cube scene in ``source``.

    Meshes are reused only when the source grid and the isovalue, stride,
    material, and color-cube settings still match. A later call with a color
    cube replaces an uncolored cache.
    """
    bundles: list[Any] = []
    if hasattr(source, "frames"):
        bundles.extend(
            frame.bundle
            for frame in source.frames
            if getattr(frame, "bundle", None) is not None
        )
    elif hasattr(source, "scene"):
        bundles.append(source)

    for bundle in bundles:
        cube = getattr(bundle, "cube_data", None)
        scene = getattr(bundle, "scene", None)
        if cube is None and isinstance(scene, dict):
            cube = scene.get("cube_data")
        if cube is None:
            continue
        surface_material = _surface_material(
            material,
            ambient=ambient,
            diffuse=diffuse,
            specular=specular,
            shininess=shininess,
            opacity=opacity,
            light_scale=light_scale,
        )
        token = isosurface_preparation_token(
            cube,
            isovalue=isovalue,
            opacity=surface_material.opacity,
            positive_color=positive_color,
            negative_color=negative_color,
            stride=stride,
            ambient=surface_material.ambient,
            diffuse=surface_material.diffuse,
            specular=surface_material.specular,
            shininess=surface_material.shininess,
            light_scale=surface_material.light_scale,
            color_cube=color_cube,
            color_range=color_range,
            color_center=color_center,
            colormap=colormap,
        )
        cached = scene.get("isosurfaces") if isinstance(scene, dict) else None
        cached_token = (
            scene.get("isosurface_preparation") if isinstance(scene, dict) else None
        )
        if cached and cached_token == token:
            cube.surface_meshes = cached
            for cached_scene in (getattr(bundle, "scene_cache", {}) or {}).values():
                if (
                    isinstance(cached_scene, dict)
                    and cached_scene.get("isosurface_preparation") != token
                ):
                    cached_scene["isosurfaces"] = cached
                    cached_scene["isosurface_preparation"] = token
            continue
        meshes = cube_isosurface_meshes(
            cube,
            isovalue=isovalue,
            opacity=surface_material.opacity,
            positive_color=positive_color,
            negative_color=negative_color,
            stride=stride,
            material=surface_material,
            color_cube=color_cube,
            color_range=color_range,
            color_center=color_center,
            colormap=colormap,
        )
        cube.surface_meshes = meshes
        if isinstance(scene, dict):
            scene["isosurfaces"] = meshes
            scene["isosurface_preparation"] = token
        for cached_scene in (getattr(bundle, "scene_cache", {}) or {}).values():
            if isinstance(cached_scene, dict):
                cached_scene["isosurfaces"] = meshes
                cached_scene["isosurface_preparation"] = token
    return source


__all__ = ["cube_isosurface_meshes", "ensure_cube_isosurfaces"]
