"""CPU isosurface extraction for Cube inputs.

scikit-image is imported only when an isosurface is requested.  The returned
mesh dictionaries are backend-neutral and can be consumed by RenderPlan.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .color_field import isosurface_preparation_token, vertex_colors_for_surface
from .io import CubeData, default_isovalue
from .smooth import (
    TAUBIN_ITERATIONS,
    area_weighted_vertex_normals,
    boundary_vertex_mask,
    scalar_gradient_normals,
    taubin_smooth,
)


def cube_isosurface_meshes(
    cube: CubeData,
    *,
    isovalue: float | None = None,
    percentile: float = 98.5,
    stride: int = 1,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    opacity: float = 0.55,
    ambient: float = 0.68,
    diffuse: float = 0.32,
    color_cube: CubeData | None = None,
    color_range: tuple[float, float] | None = None,
    color_center: float | None = None,
    colormap: str = "bgr",
    smooth: bool = False,
    smooth_iterations: int = TAUBIN_ITERATIONS,
) -> list[dict[str, Any]]:
    """Extract positive/negative phases as separate world-space meshes.

    ``color_cube`` is sampled at each vertex's physical position on the full
    color grid. ``stride`` coarsens the marching-cubes mesh only. Taubin
    smoothing, when enabled, is a visual filter applied after the isovalue
    is extracted: it does not change which voxels are inside the surface.
    Each phase is smoothed on its own. Vertices on the cube-box boundary
    stay pinned. A second cube's colors are resampled after the vertices move.
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
    if not 0.0 <= float(opacity) <= 1.0:
        raise ValueError("Cube isosurface opacity must lie in [0, 1]")
    if not np.isfinite(ambient) or float(ambient) < 0.0:
        raise ValueError("Cube isosurface ambient coefficient must be non-negative")
    if not np.isfinite(diffuse) or float(diffuse) < 0.0:
        raise ValueError("Cube isosurface diffuse coefficient must be non-negative")
    if float(ambient) + float(diffuse) > 1.0:
        raise ValueError(
            "Cube isosurface ambient + diffuse coefficients must not exceed 1"
        )
    if int(smooth_iterations) != smooth_iterations or int(smooth_iterations) < 0:
        raise ValueError("isosurface smooth iterations must be a non-negative integer")
    smooth_iterations = int(smooth_iterations)
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
        skimage_normals = normals @ inverse_normal
        lengths = np.linalg.norm(skimage_normals, axis=1)
        valid = lengths > 1.0e-12
        skimage_normals[valid] /= lengths[valid, None]
        if smooth and smooth_iterations > 0:
            pinned = boundary_vertex_mask(vertices, values.shape)
            world_vertices = taubin_smooth(
                world_vertices,
                faces,
                pinned=pinned,
                iterations=smooth_iterations,
            )
            # Negative μ can step a vertex past the data box. Pull those
            # samples back onto the marched grid before normals and colors.
            world_vertices = _contain_smoothed_vertices(
                world_vertices, basis, values.shape
            )
            mesh_normals = area_weighted_vertex_normals(
                world_vertices,
                faces,
                orient_like=skimage_normals,
            )
            index_vertices = world_vertices @ np.linalg.inv(basis)
            world_normals = scalar_gradient_normals(
                values,
                index_vertices,
                world_from_index=basis,
                fallback=mesh_normals,
            )
        else:
            world_normals = skimage_normals
        mesh = {
            "id": semantic_id,
            "name": "+ orbital" if level > 0 else "- orbital",
            "vertices": world_vertices,
            "triangles": np.asarray(faces, dtype=np.int64),
            "normals": world_normals,
            "color": color,
            "opacity": float(opacity),
            "metadata": {
                "material": {
                    "ambient": float(ambient),
                    "diffuse": float(diffuse),
                }
            },
        }
        if color_cube is not None:
            # Resample at the vertices that will be drawn. Smoothing moves
            # them off the original marching-cubes samples.
            sample_index = world_vertices @ np.linalg.inv(basis)
            mesh["vertex_colors"] = vertex_colors_for_surface(
                cube,
                color_cube,
                sample_index,
                stride=stride,
                color_range=color_range,
                color_center=color_center,
                colormap=colormap,
            )
        meshes.append(mesh)
    if not meshes:
        raise ValueError(f"Cube has no surface crossing isovalue {iso:g}")
    return meshes


def _contain_smoothed_vertices(
    world: np.ndarray,
    basis: np.ndarray,
    grid_shape: tuple[int, ...],
) -> np.ndarray:
    """Keep smoothed vertices inside the marched index box.

    Vertices that stayed inside are returned unchanged. Vertices that left
    the box are placed on the nearest face so a later color sample still
    refers to the vertex that is drawn.
    """

    points = np.asarray(world, dtype=float)
    index = points @ np.linalg.inv(basis)
    upper = np.asarray(grid_shape, dtype=float) - 1.0
    outside = np.any((index < -1.0e-8) | (index > upper + 1.0e-8), axis=1)
    if not np.any(outside):
        return points
    clipped = np.array(index, copy=True)
    clipped[outside] = np.clip(index[outside], 0.0, upper)
    contained = np.array(points, copy=True)
    contained[outside] = clipped[outside] @ basis
    return contained


def ensure_cube_isosurfaces(
    source: Any,
    *,
    isovalue: float | None = None,
    opacity: float = 0.55,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    stride: int = 2,
    smooth: bool = False,
    smooth_iterations: int = TAUBIN_ITERATIONS,
    ambient: float = 0.68,
    diffuse: float = 0.32,
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
        token = isosurface_preparation_token(
            cube,
            isovalue=isovalue,
            opacity=opacity,
            positive_color=positive_color,
            negative_color=negative_color,
            stride=stride,
            smooth=smooth,
            smooth_iterations=smooth_iterations if smooth else 0,
            ambient=ambient,
            diffuse=diffuse,
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
            opacity=opacity,
            positive_color=positive_color,
            negative_color=negative_color,
            stride=stride,
            smooth=smooth,
            smooth_iterations=smooth_iterations,
            ambient=ambient,
            diffuse=diffuse,
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
