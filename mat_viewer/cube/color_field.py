"""Sample a second cube onto isosurface vertices.

The color grid is a scalar field, not a chemical model. Vertices are located
in the source cube's physical frame (origin plus voxel axes) and converted
into the color cube's own index space. Array shape is not a grid identity.
"""

from __future__ import annotations

from hashlib import sha256

import numpy as np

from .io import CubeData

# Index slack for floating-point conversion at the color-grid boundary.
COLOR_SAMPLE_INDEX_TOLERANCE = 1e-4
_SINGULAR_AXIS_DETERMINANT = 1.0e-12


def colormap_rgb(
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
            raise ValueError(
                "isosurface color center must lie strictly inside the range"
            )
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


def resolve_color_range(
    values: np.ndarray,
    color_range: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """Return an increasing range, or None when the finite field is constant.

    ``None`` means every vertex should use the colormap midpoint. A supplied
    range is used even when the sampled field does not vary.
    """
    if color_range is not None:
        low = float(color_range[0])
        high = float(color_range[1])
        if not np.isfinite(low) or not np.isfinite(high) or high <= low:
            raise ValueError("isosurface color range must be finite and increasing")
        return (low, high)
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise ValueError("isosurface color cube has no finite values")
    low = float(np.min(finite))
    high = float(np.max(finite))
    if high <= low:
        return None
    return (low, high)


def _vector(value: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite 3-vector")
    return array


def _axes(value: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (3, 3) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite 3x3 voxel matrix")
    return array


def sample_color_cube(
    source: CubeData,
    color: CubeData,
    mesh_indices: np.ndarray,
    *,
    stride: int,
) -> np.ndarray:
    """Sample ``color`` at strided marching-cubes indices of ``source``.

    ``mesh_indices`` are in the index space of ``source.values[::stride]``.
    Physical position is ``source.origin + full_index @ source.axes``. The
    color value is the trilinear sample of the full-resolution color grid at
    the matching color-cube index. Vertices outside that grid are an error.
    """
    points = np.asarray(mesh_indices, dtype=float)
    if points.size == 0:
        return np.empty(0, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("mesh vertices must have shape (N, 3)")
    values = np.asarray(color.values, dtype=float)
    if values.ndim != 3:
        raise ValueError("isosurface color cube values must be a 3-D grid")
    full_index = points * float(stride)
    source_origin = _vector(source.origin, "source cube origin")
    source_axes = _axes(source.axes, "source cube axes")
    lab = source_origin + full_index @ source_axes
    color_origin = _vector(color.origin, "color cube origin")
    color_axes = _axes(color.axes, "color cube axes")
    determinant = float(np.linalg.det(color_axes))
    if not np.isfinite(determinant) or abs(determinant) < _SINGULAR_AXIS_DETERMINANT:
        raise ValueError("isosurface color cube axes are singular")
    color_index = (lab - color_origin) @ np.linalg.inv(color_axes)
    maximum = np.asarray(values.shape, dtype=float) - 1.0
    outside = (color_index < -COLOR_SAMPLE_INDEX_TOLERANCE) | (
        color_index > maximum + COLOR_SAMPLE_INDEX_TOLERANCE
    )
    if np.any(outside):
        raise ValueError(
            "isosurface color cube does not cover the surface; compare its "
            "origin, voxel axes, and shape with the source grid"
        )
    return _sample_trilinear(values, color_index)


def vertex_colors_for_surface(
    source: CubeData,
    color: CubeData,
    mesh_indices: np.ndarray,
    *,
    stride: int,
    color_range: tuple[float, float] | None,
    color_center: float | None,
    colormap: str,
) -> np.ndarray:
    """Return RGB samples for one isosurface phase.

    A finite constant color cube and no explicit range maps every vertex to
    the colormap midpoint. A nonfinite voxel with nonzero interpolation
    weight makes that sample an error. A nonfinite voxel with zero weight,
    including the unused corners of an exact grid point or grid face, is
    ignored.
    """
    samples = sample_color_cube(source, color, mesh_indices, stride=stride)
    if samples.size and not np.all(np.isfinite(samples)):
        count = int(np.count_nonzero(~np.isfinite(samples)))
        raise ValueError(
            "isosurface color cube produced "
            f"{count} nonfinite samples; nonfinite color values are not mapped"
        )
    resolved = resolve_color_range(np.asarray(color.values, dtype=float), color_range)
    if resolved is None:
        return colormap_rgb(colormap, np.full(samples.shape, 0.5), 0.0, 1.0)
    return colormap_rgb(
        colormap, samples, resolved[0], resolved[1], color_center
    )


def _sample_trilinear(grid: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Sample ``grid`` at floating index coordinates.

    Corners with zero weight are not read. ``NaN * 0`` would otherwise
    poison an exact grid point or face when an unused corner is nonfinite.
    A nonfinite corner with positive weight still makes the sample nonfinite.
    """
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
    corners = (
        (grid[i0, j0, k0], (1.0 - di) * (1.0 - dj) * (1.0 - dk)),
        (grid[i1, j0, k0], di * (1.0 - dj) * (1.0 - dk)),
        (grid[i0, j1, k0], (1.0 - di) * dj * (1.0 - dk)),
        (grid[i1, j1, k0], di * dj * (1.0 - dk)),
        (grid[i0, j0, k1], (1.0 - di) * (1.0 - dj) * dk),
        (grid[i1, j0, k1], di * (1.0 - dj) * dk),
        (grid[i0, j1, k1], (1.0 - di) * dj * dk),
        (grid[i1, j1, k1], di * dj * dk),
    )
    total = np.zeros(di.shape, dtype=float)
    nonfinite = np.zeros(di.shape, dtype=bool)
    for value, weight in corners:
        active = weight > 0.0
        finite = np.isfinite(value)
        nonfinite |= active & ~finite
        usable = active & finite
        total[usable] += weight[usable] * value[usable]
    total[nonfinite] = np.nan
    return total


def _grid_digest(grid: CubeData) -> str:
    values = np.ascontiguousarray(np.asarray(grid.values), dtype="<f8")
    origin = np.ascontiguousarray(_vector(grid.origin, "cube origin"), dtype="<f8")
    axes = np.ascontiguousarray(_axes(grid.axes, "cube axes"), dtype="<f8")
    digest = sha256()
    digest.update(np.asarray(values.shape, dtype="<i8").tobytes())
    digest.update(values.tobytes())
    digest.update(origin.tobytes())
    digest.update(axes.tobytes())
    return digest.hexdigest()


def isosurface_preparation_token(
    cube: CubeData,
    *,
    isovalue: float | None,
    opacity: float,
    positive_color: str,
    negative_color: str,
    stride: int,
    ambient: float,
    diffuse: float,
    specular: float,
    shininess: float,
    light_scale: float,
    color_cube: CubeData | None,
    color_range: tuple[float, float] | None,
    color_center: float | None,
    colormap: str,
) -> str:
    """Identity of one prepared isosurface, including the color grid."""
    payload = (
        _grid_digest(cube),
        None if color_cube is None else _grid_digest(color_cube),
        None if isovalue is None else float(isovalue),
        float(opacity),
        str(positive_color),
        str(negative_color),
        int(stride),
        float(ambient),
        float(diffuse),
        float(specular),
        float(shininess),
        float(light_scale),
        None
        if color_range is None
        else (float(color_range[0]), float(color_range[1])),
        None if color_center is None else float(color_center),
        str(colormap),
    )
    return sha256(repr(payload).encode("utf-8")).hexdigest()


__all__ = [
    "COLOR_SAMPLE_INDEX_TOLERANCE",
    "colormap_rgb",
    "isosurface_preparation_token",
    "resolve_color_range",
    "sample_color_cube",
    "vertex_colors_for_surface",
]
