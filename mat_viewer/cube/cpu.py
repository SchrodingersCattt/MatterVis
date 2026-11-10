"""CPU isosurface extraction for Cube inputs.

scikit-image is imported only when an isosurface is requested.  The returned
mesh dictionaries are backend-neutral and can be consumed by RenderPlan.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .io import CubeData, default_isovalue


def _sample_trilinear(grid: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Sample ``grid`` at floating index coordinates."""
    if points.size == 0:
        return np.empty(0, dtype=float)
    maximum = np.asarray(grid.shape, dtype=float) - 1.0
    coords = np.clip(np.asarray(points, dtype=float), 0.0, maximum)
    base = np.floor(coords).astype(int)
    upper = np.minimum(base + 1, np.asarray(grid.shape, dtype=int) - 1)
    fraction = coords - base
    i0, j0, k0 = base.T
    i1, j1, k1 = upper.T
    di, dj, dk = fraction.T
    c000 = grid[i0, j0, k0]
    c100 = grid[i1, j0, k0]
    c010 = grid[i0, j1, k0]
    c110 = grid[i1, j1, k0]
    c001 = grid[i0, j0, k1]
    c101 = grid[i1, j0, k1]
    c011 = grid[i0, j1, k1]
    c111 = grid[i1, j1, k1]
    c00 = c000 * (1.0 - di) + c100 * di
    c01 = c001 * (1.0 - di) + c101 * di
    c10 = c010 * (1.0 - di) + c110 * di
    c11 = c011 * (1.0 - di) + c111 * di
    c0 = c00 * (1.0 - dj) + c10 * dj
    c1 = c01 * (1.0 - dj) + c11 * dj
    return c0 * (1.0 - dk) + c1 * dk


def _colormap_rgb(
    name: str,
    values: np.ndarray,
    vmin: float,
    vmax: float,
    center: float | None = None,
) -> np.ndarray:
    """Map scalars into RGB. ``bgr`` runs blue, green, red.

    ``center`` is placed at the middle of the colormap. Use 0 for a signed
    field whose neutral value should stay green when the numeric range is
    asymmetric.
    """
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        raise ValueError("isosurface color range must be finite and increasing")
    array = np.asarray(values, dtype=float)
    if center is None:
        normalized = (array - vmin) / (vmax - vmin)
    else:
        if not np.isfinite(center) or not vmin < center < vmax:
            raise ValueError("isosurface color center must lie strictly inside the range")
        normalized = np.empty_like(array)
        low = array <= center
        normalized[low] = 0.5 * (array[low] - vmin) / (center - vmin)
        normalized[~low] = 0.5 + 0.5 * (array[~low] - center) / (vmax - center)
    normalized = np.clip(normalized, 0.0, 1.0)
    if name == "bgr":
        rgb = np.zeros((normalized.size, 3), dtype=float)
        low = normalized <= 0.5
        rising = np.zeros_like(normalized)
        rising[low] = normalized[low] / 0.5
        rising[~low] = (normalized[~low] - 0.5) / 0.5
        rgb[low, 1] = rising[low]
        rgb[low, 2] = 1.0 - rising[low]
        rgb[~low, 0] = rising[~low]
        rgb[~low, 1] = 1.0 - rising[~low]
        return rgb
    import matplotlib

    try:
        cmap = matplotlib.colormaps[name]
    except KeyError as exc:
        raise ValueError(f"unknown isosurface colormap {name!r}") from exc
    return np.asarray(cmap(normalized), dtype=float)[:, :3]


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
    color_values: np.ndarray | None = None,
    color_range: tuple[float, float] | None = None,
    color_center: float | None = None,
    colormap: str = "bgr",
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
    if not np.isfinite(ambient) or float(ambient) < 0.0:
        raise ValueError("Cube isosurface ambient coefficient must be non-negative")
    if not np.isfinite(diffuse) or float(diffuse) < 0.0:
        raise ValueError("Cube isosurface diffuse coefficient must be non-negative")
    if float(ambient) + float(diffuse) > 1.0:
        raise ValueError(
            "Cube isosurface ambient + diffuse coefficients must not exceed 1"
        )
    color_grid = None
    if color_values is not None:
        color_grid = np.asarray(color_values, dtype=float)
        if color_grid.shape != cube.values.shape:
            raise ValueError(
                "isosurface color cube must have the same grid shape as the source cube"
            )
        color_grid = color_grid[::stride, ::stride, ::stride]
        if color_range is None:
            finite = color_grid[np.isfinite(color_grid)]
            if finite.size == 0:
                raise ValueError("isosurface color cube has no finite values")
            color_range = (float(np.min(finite)), float(np.max(finite)))

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
            "opacity": float(opacity),
            "metadata": {
                "material": {
                    "ambient": float(ambient),
                    "diffuse": float(diffuse),
                }
            },
        }
        if color_grid is not None:
            if color_range is None:
                raise ValueError("isosurface color range is missing")
            sampled = _sample_trilinear(color_grid, vertices)
            mesh["vertex_colors"] = _colormap_rgb(
                colormap, sampled, color_range[0], color_range[1], color_center
            )
        meshes.append(mesh)
    if not meshes:
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
    color_values: np.ndarray | None = None,
    color_range: tuple[float, float] | None = None,
    color_center: float | None = None,
    colormap: str = "bgr",
) -> Any:
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
        meshes = cube_isosurface_meshes(
            cube, isovalue=isovalue, opacity=opacity,
            positive_color=positive_color, negative_color=negative_color,
            stride=stride, ambient=ambient, diffuse=diffuse,
            color_values=color_values, color_range=color_range,
            color_center=color_center, colormap=colormap,
        )
        setattr(cube, "surface_meshes", meshes)
        if isinstance(scene, dict):
            scene["isosurfaces"] = meshes
        for cached_scene in (getattr(bundle, "scene_cache", {}) or {}).values():
            if isinstance(cached_scene, dict):
                cached_scene["isosurfaces"] = meshes
    return source


__all__ = ["cube_isosurface_meshes", "ensure_cube_isosurfaces"]
