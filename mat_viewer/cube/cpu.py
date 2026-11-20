"""CPU isosurface extraction for Cube inputs.

scikit-image is imported only when an isosurface is requested.  The returned
mesh dictionaries are backend-neutral and can be consumed by RenderPlan.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .color_field import isosurface_preparation_token, vertex_colors_for_surface
from .density_mask import filter_mesh_by_density, resolve_density_mask
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
    ambient: float = 0.68,
    diffuse: float = 0.32,
    color_cube: CubeData | None = None,
    color_range: tuple[float, float] | None = None,
    color_center: float | None = None,
    colormap: str = "bgr",
    density_mask: bool = False,
    density_cube: CubeData | None = None,
    density_cutoff: float | None = None,
) -> list[dict[str, Any]]:
    """Extract positive/negative phases as world-space triangle meshes.

    ``color_cube`` is sampled at each vertex's physical position on the full
    color grid. ``stride`` coarsens the marching-cubes mesh only.

    ``density_mask`` is off unless set. While it is off, ``density_cube`` and
    ``density_cutoff`` are ignored, including when both are present. When it
    is on, both are required. Triangles are clipped to ρ ≤ ρ_cut. A crossing
    edge keeps the linearly interpolated vertex instead of dropping the
    whole triangle, which would follow the marching-cubes stride. The
    returned vertices are the visible mesh. The field, density, and colour
    cubes are not rewritten, and the colour range is not taken from the
    vertices that remain. The cutoff is not chosen here.
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
    active_mask = resolve_density_mask(
        enabled=density_mask,
        density_cube=density_cube,
        density_cutoff=density_cutoff,
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
    crossed = False
    removed_by_mask = False
    for level, color, semantic_id in phases:
        if not minimum < level < maximum:
            continue
        try:
            vertices, faces, normals, _ = marching_cubes(values, level=level)
        except (RuntimeError, ValueError):
            continue
        crossed = True
        # Cube atom coordinates are normalized by cube_to_raw_atoms into the scene frame.
        # Keep CPU isosurfaces in that same frame when cube.origin is nonzero.
        world_vertices = vertices @ basis
        world_normals = normals @ inverse_normal
        lengths = np.linalg.norm(world_normals, axis=1)
        valid = lengths > 1.0e-12
        world_normals[valid] /= lengths[valid, None]
        vertex_colors = None
        if color_cube is not None:
            vertex_colors = vertex_colors_for_surface(
                cube,
                color_cube,
                vertices,
                stride=stride,
                color_range=color_range,
                color_center=color_center,
                colormap=colormap,
            )
        triangles = np.asarray(faces, dtype=np.int64)
        metadata: dict[str, Any] = {
            "material": {
                "ambient": float(ambient),
                "diffuse": float(diffuse),
            }
        }
        if active_mask is not None:
            masked_density, cutoff = active_mask
            filtered = filter_mesh_by_density(
                cube,
                masked_density,
                vertices,
                triangles,
                (world_vertices, world_normals, vertex_colors),
                stride=stride,
                cutoff=cutoff,
            )
            if filtered is None:
                removed_by_mask = True
                continue
            triangles, (world_vertices, world_normals, vertex_colors) = filtered
            metadata["density_mask"] = {
                "cutoff_e_per_bohr3": float(cutoff),
                "rule": (
                    "clip triangles to rho <= cutoff; "
                    "edge vertices are linearly interpolated"
                ),
            }
        mesh = {
            "id": semantic_id,
            "name": "+ orbital" if level > 0 else "- orbital",
            "vertices": world_vertices,
            "triangles": triangles,
            "normals": world_normals,
            "color": color,
            "opacity": float(opacity),
            "metadata": metadata,
        }
        if vertex_colors is not None:
            mesh["vertex_colors"] = vertex_colors
        meshes.append(mesh)
    if not meshes:
        if active_mask is not None and crossed and removed_by_mask:
            cutoff = active_mask[1]
            raise ValueError(
                "density mask removed every triangle at cutoff "
                f"{cutoff:g} e/Bohr^3; no replacement cutoff was chosen"
            )
        raise ValueError(f"Cube has no surface crossing isovalue {iso:g}")
    return meshes


def ensure_cube_isosurfaces(
    source: Any,
    *,
    isovalue: float | None = None,
    opacity: float = 0.55,
    positive_color: str = "#D55E00",
    negative_color: str = "#0072B2",
    stride: int = 2,
    ambient: float = 0.68,
    diffuse: float = 0.32,
    color_cube: CubeData | None = None,
    color_range: tuple[float, float] | None = None,
    color_center: float | None = None,
    colormap: str = "bgr",
    density_mask: bool = False,
    density_cube: CubeData | None = None,
    density_cutoff: float | None = None,
) -> Any:
    """Attach backend-neutral isosurfaces to every Cube scene in ``source``.

    Meshes are reused only when the source grid and the isovalue, stride,
    material, color-cube, and active density-mask settings still match. A
    later call replaces the one stored mesh list. Cutoff values are not
    accumulated, and a density cube is not part of the identity while the
    mask is off.
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
            ambient=ambient,
            diffuse=diffuse,
            color_cube=color_cube,
            color_range=color_range,
            color_center=color_center,
            colormap=colormap,
            density_mask=density_mask,
            density_cube=density_cube,
            density_cutoff=density_cutoff,
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
            ambient=ambient,
            diffuse=diffuse,
            color_cube=color_cube,
            color_range=color_range,
            color_center=color_center,
            colormap=colormap,
            density_mask=density_mask,
            density_cube=density_cube,
            density_cutoff=density_cutoff,
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
