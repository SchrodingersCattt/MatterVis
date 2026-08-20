"""CPU isosurface extraction for Cube inputs.

scikit-image is imported only when an isosurface is requested.  The returned
mesh dictionaries are backend-neutral and can be consumed by RenderPlan.
"""

from __future__ import annotations

from typing import Any

import numpy as np

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
) -> list[dict[str, Any]]:
    """Extract positive/negative phases as world-space triangle meshes."""
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
        world_vertices = cube.origin + vertices @ basis
        world_normals = normals @ inverse_normal
        lengths = np.linalg.norm(world_normals, axis=1)
        valid = lengths > 1.0e-12
        world_normals[valid] /= lengths[valid, None]
        meshes.append(
            {
                "id": semantic_id,
                "name": "+ orbital" if level > 0 else "- orbital",
                "vertices": world_vertices,
                "triangles": np.asarray(faces, dtype=np.int64),
                "normals": world_normals,
                "color": color,
                "opacity": float(opacity),
            }
        )
    if not meshes:
        raise ValueError(f"Cube has no surface crossing isovalue {iso:g}")
    return meshes


def ensure_cube_isosurfaces(source: Any) -> Any:
    """Attach backend-neutral isosurfaces to every Cube scene in ``source``."""
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
        if isinstance(scene, dict) and scene.get("isosurfaces"):
            continue
        meshes = cube_isosurface_meshes(cube)
        setattr(cube, "surface_meshes", meshes)
        if isinstance(scene, dict):
            scene["isosurfaces"] = meshes
        for cached_scene in (getattr(bundle, "scene_cache", {}) or {}).values():
            if isinstance(cached_scene, dict):
                cached_scene["isosurfaces"] = meshes
    return source


__all__ = ["cube_isosurface_meshes", "ensure_cube_isosurfaces"]
