"""Domain-neutral implicit-surface sampling.

An implicit geometry is a scalar field ``f(x, y, z)`` together with a finite
Cartesian domain and an isovalue.  This module turns that description into a
validated triangular mesh without knowing anything about crystals, elements,
or a particular renderer.  The finite domain is intentional: most useful
implicit surfaces (planes included) are unbounded and cannot be rendered
without a clipping box.

``scikit-image`` is used when it is available because its Lewiner marching
cubes implementation handles ambiguous cells particularly well.  MatterVis
does not require that optional package, however; a small marching-tetrahedra
fallback keeps the public API usable in a minimal NumPy installation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import numpy as np

from .geometry import validate_mesh


def _coerce_bounds(bounds: Iterable[Iterable[float]]) -> np.ndarray:
    """Return finite ``[[min, max], ...]`` bounds for x, y, and z."""

    try:
        array = np.asarray(list(bounds), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "bounds must be an iterable of three finite (min, max) pairs"
        ) from exc
    if (
        array.shape != (3, 2)
        or not np.all(np.isfinite(array))
        or np.any(array[:, 1] <= array[:, 0])
    ):
        raise ValueError(
            "bounds must contain three finite pairs with max greater than min"
        )
    return array


def _coerce_resolution(resolution: int | Iterable[int]) -> tuple[int, int, int]:
    """Normalise a scalar or per-axis grid resolution."""

    if isinstance(resolution, (bool, np.bool_)):
        raise ValueError("resolution must be an integer or three integers >= 2")
    if np.isscalar(resolution):
        values = [resolution] * 3
    else:
        try:
            values = list(resolution)
        except TypeError as exc:
            raise ValueError(
                "resolution must be an integer or three integers >= 2"
            ) from exc
    if len(values) != 3:
        raise ValueError("resolution must be an integer or three integers >= 2")

    normalised: list[int] = []
    for value in values:
        if isinstance(value, (bool, np.bool_)):
            raise ValueError("resolution must be an integer or three integers >= 2")
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                "resolution must be an integer or three integers >= 2"
            ) from exc
        if isinstance(value, (float, np.floating)) and float(value) != integer:
            raise ValueError("resolution must be an integer or three integers >= 2")
        if integer < 2:
            raise ValueError("resolution must be an integer or three integers >= 2")
        normalised.append(integer)
    return tuple(normalised)  # type: ignore[return-value]


def _coerce_level(level: float) -> float:
    try:
        value = float(level)
    except (TypeError, ValueError) as exc:
        raise ValueError("level must be a finite number") from exc
    if not np.isfinite(value):
        raise ValueError("level must be a finite number")
    return value


def _normalise_field_values(
    raw: Any,
    point_count: int,
    grid_shape: tuple[int, int, int],
) -> np.ndarray:
    """Coerce a field result to the requested grid shape."""

    try:
        values = np.asarray(raw, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("implicit field must return numeric scalar values") from exc

    if values.ndim == 0:
        values = np.full(grid_shape, float(values), dtype=float)
    elif values.shape == grid_shape:
        values = np.array(values, dtype=float, copy=False)
    elif values.shape == (point_count,):
        values = values.reshape(grid_shape)
    elif values.shape == (point_count, 1):
        values = values[:, 0].reshape(grid_shape)
    elif values.size == point_count:
        values = values.reshape(grid_shape)
    else:
        raise ValueError(
            "implicit field must return one scalar per point "
            f"(expected {point_count}, got shape {values.shape})"
        )
    if not np.all(np.isfinite(values)):
        raise ValueError("implicit field returned non-finite values")
    return np.asarray(values, dtype=float)


def _evaluate_field(
    field: Callable[..., Any],
    points: np.ndarray,
    grid: np.ndarray,
) -> np.ndarray:
    """Evaluate vectorised or scalar-style field callables.

    The preferred contract is ``field(points)`` where ``points`` is ``N×3``.
    A three-array form ``field(x, y, z)`` is also accepted, followed by a
    scalar fallback for simple functions written with ``math`` operations.
    """

    point_count = len(points)
    grid_shape = tuple(int(value) for value in grid.shape[:3])
    errors: list[Exception] = []

    try:
        return _normalise_field_values(field(points), point_count, grid_shape)
    except (TypeError, ValueError, IndexError, AttributeError) as exc:
        errors.append(exc)

    try:
        return _normalise_field_values(
            field(grid[..., 0], grid[..., 1], grid[..., 2]),
            point_count,
            grid_shape,
        )
    except (TypeError, ValueError, IndexError, AttributeError) as exc:
        errors.append(exc)

    # Last-resort scalar evaluation keeps the API friendly to callables such
    # as ``lambda x, y, z: math.sqrt(x*x + y*y + z*z) - r``.  It is slower than
    # either vectorised form, so it is deliberately attempted only after both
    # vectorised contracts have failed.
    scalar_values = np.empty(point_count, dtype=float)
    try:
        for index, point in enumerate(points):
            try:
                value = field(float(point[0]), float(point[1]), float(point[2]))
            except (TypeError, ValueError, IndexError, AttributeError):
                value = field(point)
            scalar_values[index] = float(np.asarray(value).squeeze())
        return _normalise_field_values(scalar_values, point_count, grid_shape)
    except (TypeError, ValueError, IndexError, AttributeError) as exc:
        errors.append(exc)

    detail = str(errors[-1]) if errors else "unknown field error"
    message = (
        "implicit field must accept field(points), field(x, y, z), "
        f"or scalar xyz calls ({detail})"
    )
    if errors:
        raise ValueError(message) from errors[-1]
    raise ValueError(message)


def _tetra_gradient(points: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Estimate the constant scalar-field gradient inside a tetrahedron."""

    try:
        return np.linalg.solve(points[1:] - points[0], values[1:] - values[0])
    except np.linalg.LinAlgError:
        return np.zeros(3, dtype=float)


def _ordered_polygon(
    points: np.ndarray,
    gradient: np.ndarray,
) -> list[int]:
    """Return polygon vertex indices in a stable cyclic order."""

    if len(points) <= 3:
        return list(range(len(points)))
    centre = np.mean(points, axis=0)
    normal = np.asarray(gradient, dtype=float)
    if np.linalg.norm(normal) <= 1e-14:
        normal = np.cross(points[1] - points[0], points[2] - points[0])
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-14:
        return list(range(len(points)))
    normal /= normal_norm
    basis_u = points[0] - centre
    if np.linalg.norm(basis_u) <= 1e-14:
        basis_u = points[1] - centre
    basis_u /= float(np.linalg.norm(basis_u))
    basis_v = np.cross(normal, basis_u)
    angles = np.arctan2(
        (points - centre) @ basis_v,
        (points - centre) @ basis_u,
    )
    return np.argsort(angles).tolist()


def _marching_tetrahedra(
    values: np.ndarray,
    bounds: np.ndarray,
    level: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Dependency-free isosurface extraction used when skimage is absent."""

    nx, ny, nz = (int(value) for value in values.shape)
    axes = [
        np.linspace(bounds[axis, 0], bounds[axis, 1], values.shape[axis])
        for axis in range(3)
    ]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1)
    flat_points = grid.reshape(-1, 3)
    flat_values = values.reshape(-1)

    def vertex_index(i: int, j: int, k: int) -> int:
        return (i * ny + j) * nz + k

    # Six tetrahedra around the cube's 0--6 body diagonal.  This decomposition
    # shares every cube face consistently and avoids cracks between cells.
    tetrahedra = (
        (0, 1, 2, 6),
        (0, 2, 3, 6),
        (0, 3, 7, 6),
        (0, 7, 4, 6),
        (0, 4, 5, 6),
        (0, 5, 1, 6),
    )
    tetra_edges = (
        (0, 1),
        (0, 2),
        (0, 3),
        (1, 2),
        (1, 3),
        (2, 3),
    )

    mesh_vertices: list[np.ndarray] = []
    mesh_faces: list[tuple[int, int, int]] = []
    edge_cache: dict[tuple[int, int], int] = {}
    scale = max(1.0, float(np.max(np.abs(values))), abs(level))
    zero_tolerance = 32.0 * np.finfo(float).eps * scale

    for i in range(nx - 1):
        for j in range(ny - 1):
            for k in range(nz - 1):
                cube = [
                    vertex_index(i, j, k),
                    vertex_index(i + 1, j, k),
                    vertex_index(i + 1, j + 1, k),
                    vertex_index(i, j + 1, k),
                    vertex_index(i, j, k + 1),
                    vertex_index(i + 1, j, k + 1),
                    vertex_index(i + 1, j + 1, k + 1),
                    vertex_index(i, j + 1, k + 1),
                ]
                for tetra in tetrahedra:
                    ids = [cube[index] for index in tetra]
                    signed = flat_values[ids] - level
                    # Exact grid hits are treated as the outside side.  The
                    # interpolation still lands exactly on the zero vertex,
                    # while avoiding duplicate zero-length edge crossings.
                    inside = signed < -zero_tolerance
                    if bool(np.all(inside)) or not bool(np.any(inside)):
                        continue
                    gradient = _tetra_gradient(flat_points[ids], signed)
                    local_indices: list[int] = []
                    for edge_a, edge_b in tetra_edges:
                        if bool(inside[edge_a]) == bool(inside[edge_b]):
                            continue
                        id_a, id_b = ids[edge_a], ids[edge_b]
                        key = (min(id_a, id_b), max(id_a, id_b))
                        point_index = edge_cache.get(key)
                        if point_index is None:
                            value_a = signed[edge_a]
                            value_b = signed[edge_b]
                            denominator = value_b - value_a
                            if abs(float(denominator)) <= np.finfo(float).eps:
                                continue
                            fraction = float(np.clip(-value_a / denominator, 0.0, 1.0))
                            point = flat_points[id_a] + fraction * (
                                flat_points[id_b] - flat_points[id_a]
                            )
                            point_index = len(mesh_vertices)
                            mesh_vertices.append(point)
                            edge_cache[key] = point_index
                        if point_index not in local_indices:
                            local_indices.append(point_index)

                    if len(local_indices) < 3:
                        continue
                    local_points = np.asarray(
                        [mesh_vertices[index] for index in local_indices], dtype=float
                    )
                    order = _ordered_polygon(local_points, gradient)
                    ordered = [local_indices[index] for index in order]
                    if len(ordered) == 3:
                        candidate_faces = [tuple(ordered)]
                    else:
                        candidate_faces = [
                            (ordered[0], ordered[1], ordered[2]),
                            (ordered[0], ordered[2], ordered[3]),
                        ]
                    for face in candidate_faces:
                        p0, p1, p2 = (mesh_vertices[index] for index in face)
                        normal = np.cross(p1 - p0, p2 - p0)
                        if np.linalg.norm(normal) <= 1e-12:
                            continue
                        if np.linalg.norm(gradient) > 1e-14 and np.dot(normal, gradient) < 0:
                            face = (face[0], face[2], face[1])
                        mesh_faces.append(face)

    if not mesh_faces:
        raise ValueError(f"implicit field does not cross level {level:g} in bounds")
    return validate_mesh(np.asarray(mesh_vertices, dtype=float), mesh_faces)


def implicit_surface_mesh(
    field: Callable[..., Any],
    bounds: Iterable[Iterable[float]],
    *,
    level: float = 0.0,
    resolution: int | Iterable[int] = 32,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample an implicit scalar field and return a triangular isosurface.

    Parameters
    ----------
    field:
        A callable using one of three compatible forms: ``field(points)``
        where points is ``N×3`` (preferred), ``field(x, y, z)`` with broadcast
        arrays, or a scalar ``field(x, y, z)`` callable.  The zero/``level``
        surface is extracted from its returned scalar values.
    bounds:
        Finite Cartesian clipping box ``((xmin, xmax), (ymin, ymax),
        (zmin, zmax))``.  It is required even for an unbounded plane.
    level:
        Isovalue to extract, defaulting to zero.
    resolution:
        Number of grid samples along each axis, either one integer or a
        three-integer tuple.  Higher values improve curvature at the cost of
        sampling time and mesh size.

    Returns
    -------
    (vertices, faces): tuple[numpy.ndarray, numpy.ndarray]
        Finite ``N×3`` Cartesian vertices and ``M×3`` triangular indices.

    Raises
    ------
    ValueError
        If the field cannot be evaluated, the domain is malformed, or no
        isosurface crosses the requested clipping box.
    """

    if not callable(field):
        raise ValueError("field must be callable")
    bounds_array = _coerce_bounds(bounds)
    resolution_tuple = _coerce_resolution(resolution)
    level_value = _coerce_level(level)
    axes = [
        np.linspace(bounds_array[axis, 0], bounds_array[axis, 1], resolution_tuple[axis])
        for axis in range(3)
    ]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1)
    points = grid.reshape(-1, 3)
    values = _evaluate_field(field, points, grid)

    try:
        from skimage.measure import marching_cubes
    except ImportError:
        marching_cubes = None

    if marching_cubes is not None:
        try:
            vertices, faces, _normals, _samples = marching_cubes(
                values,
                level=level_value,
                spacing=tuple(
                    (bounds_array[axis, 1] - bounds_array[axis, 0])
                    / (resolution_tuple[axis] - 1)
                    for axis in range(3)
                ),
                allow_degenerate=False,
            )
            vertices = np.asarray(vertices, dtype=float) + bounds_array[:, 0]
            return validate_mesh(vertices, np.asarray(faces, dtype=int))
        except (ValueError, RuntimeError) as exc:
            # A range error means there is no crossing.  For an unusual
            # backend failure, the dependency-free implementation is still a
            # useful fallback and gives the same public error semantics.
            backend_error = exc
        else:  # pragma: no cover - marching_cubes always returns or raises
            backend_error = None
    else:
        backend_error = None

    try:
        return _marching_tetrahedra(values, bounds_array, level_value)
    except ValueError:
        if backend_error is not None:
            raise ValueError(
                f"implicit field does not cross level {level_value:g} in bounds"
            ) from backend_error
        raise


__all__ = ["implicit_surface_mesh"]
