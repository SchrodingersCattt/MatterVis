"""Cube-frame geometry for the experimental PyVista figure.

Bond segments, cell edges, repetition, sampling, and the orthographic
view rig live here so ``vtk_cube`` can stay the figure entry. This module
does not choose elements, radii, or bond cutoffs. The caller supplies
those values. Positions stay in the cube frame ``origin + index @ axes``.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..cube.color_field import (
    _SINGULAR_AXIS_DETERMINANT,
    _sample_trilinear,
)
from ..cube.io import default_isovalue

# Distance is a multiple of the scene radius (half the bounds diagonal).
# Light offsets are fractions of that distance in the view basis
# (along look, along up, along side). Parallel projection does not use the
# distance for framing; the distance keeps the camera and the lights outside
# the geometry.
_CAMERA_DISTANCE_IN_RADII = 4.0
_LIGHTS = (
    ((0.50, 0.00, 0.00), 1.35),
    ((0.25, 0.375, 0.125), 1.05),
    ((0.1875, -0.25, -0.25), 0.75),
)
_SCALAR = "color_value"
_MIN_SCENE_RADIUS = 1.0e-6
# Corner cycle, fractional axis, and side. side=-1 is the f=0 face and
# side=+1 is the f=1 face. Outward normals come from the reciprocal lattice,
# not from this winding.
_CELL_FACES = (
    ((0, 2, 6, 3), 0, -1),
    ((1, 4, 7, 5), 0, 1),
    ((0, 1, 5, 3), 1, -1),
    ((2, 4, 7, 6), 1, 1),
    ((0, 1, 4, 2), 2, -1),
    ((3, 5, 7, 6), 2, 1),
)



def _bond_segments(
    xyz,
    atomic_numbers,
    bonds,
    element_color,
    bond_color,
    element_radius=None,
):
    """Group bond line segments by RGB color.

    A uniform ``bond_color`` keeps one full stick per pair. ``None`` splits
    each bond at the midpoint and paints each half with the endpoint atom
    color. When ``element_radius`` is set, each half starts at the atom
    sphere surface so the color join sits in the visible gap.
    """

    groups: dict[tuple[float, float, float], dict[str, list]] = {}
    for left, right in bonds:
        i = int(left)
        j = int(right)
        start = np.asarray(xyz[i], dtype=float)
        end = np.asarray(xyz[j], dtype=float)
        if bond_color is None:
            delta = end - start
            length = float(np.linalg.norm(delta))
            if length <= 0.0:
                continue
            unit = delta / length
            mid = 0.5 * (start + end)
            halves = []
            for index, center, outward in (
                (i, start, unit),
                (j, end, -unit),
            ):
                color = tuple(float(c) for c in element_color[int(atomic_numbers[index])])
                radius = 0.0
                if element_radius is not None:
                    radius = float(element_radius[int(atomic_numbers[index])])
                # Keep a short stub if the sphere reaches past the midpoint.
                inset = min(max(radius, 0.0), 0.5 * length - 1e-6)
                halves.append((color, center + outward * inset, mid))
        else:
            halves = ((tuple(float(c) for c in bond_color), start, end),)
        for color, point_a, point_b in halves:
            if float(np.linalg.norm(np.asarray(point_a) - np.asarray(point_b))) <= 1e-9:
                continue
            bucket = groups.setdefault(color, {"points": [], "lines": [], "cursor": 0})
            bucket["points"].extend([point_a, point_b])
            bucket["lines"].append([2, bucket["cursor"], bucket["cursor"] + 1])
            bucket["cursor"] += 2
    return groups


def _cell_box(origin, vectors) -> tuple[np.ndarray, np.ndarray] | None:
    if origin is None and vectors is None:
        return None
    if origin is None or vectors is None:
        raise ValueError("cell_origin and cell_vectors must both be set or both omitted")
    corner = _as_vector(origin, "cell_origin")
    lattice = _as_axes(vectors)
    return corner, lattice


def _cell_corners(origin: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Eight corners of a parallelepiped whose lattice vectors are rows."""

    return np.array(
        [
            origin,
            origin + vectors[0],
            origin + vectors[1],
            origin + vectors[2],
            origin + vectors[0] + vectors[1],
            origin + vectors[0] + vectors[2],
            origin + vectors[1] + vectors[2],
            origin + vectors[0] + vectors[1] + vectors[2],
        ],
        dtype=float,
    )


def _camera_face(vectors: np.ndarray, direction: np.ndarray) -> tuple[int, ...]:
    """Return the face whose outward normal points toward the camera.

    ``direction`` points from the camera toward the scene. The outward normal
    of the f=0 face points against that fractional axis, and the f=1 face
    points along it, including left-handed cells. The near face maximizes
    the alignment of that outward normal with ``-direction``.
    """

    inverse = np.linalg.inv(np.asarray(vectors, dtype=float))
    best_face = None
    best_score = -np.inf
    for cycle, axis, side in _CELL_FACES:
        outward = float(side) * np.asarray(inverse[axis], dtype=float)
        length = float(np.linalg.norm(outward))
        if length < 1.0e-12:
            continue
        score = float(np.dot(outward / length, -direction))
        if score > best_score:
            best_score = score
            best_face = cycle
    if best_face is None:
        raise ValueError("cell faces are degenerate")
    return best_face


def _cell_edge_polydata(
    pv,
    origin: np.ndarray,
    vectors: np.ndarray,
    *,
    look: np.ndarray | None = None,
    mode: str = "box",
):
    corners = _cell_corners(origin, vectors)
    edges = (
        (0, 1),
        (0, 2),
        (0, 3),
        (1, 4),
        (1, 5),
        (2, 4),
        (2, 6),
        (3, 5),
        (3, 6),
        (4, 7),
        (5, 7),
        (6, 7),
    )
    if mode == "face":
        if look is None:
            raise ValueError("cell_edge_mode='face' requires look")
        direction = np.asarray(look, dtype=float)
        norm = float(np.linalg.norm(direction))
        if norm < 1.0e-12:
            raise ValueError("cell_edge_mode='face' requires a non-zero look")
        face = _camera_face(vectors, direction / norm)
        edges = (
            (face[0], face[1]),
            (face[1], face[2]),
            (face[2], face[3]),
            (face[3], face[0]),
        )
    points = []
    lines = []
    cursor = 0
    for left, right in edges:
        points.extend([corners[left], corners[right]])
        lines.append([2, cursor, cursor + 1])
        cursor += 2
    return pv.PolyData(np.asarray(points, dtype=float), lines=np.hstack(lines))


def _positive_power(name: str, value) -> float:
    power = float(value)
    if not np.isfinite(power) or power <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return power


def _closed_values(values: np.ndarray, periodic: bool) -> np.ndarray:
    """Append the index-0 plane so a periodic grid closes at index N."""

    if not periodic:
        return values
    return np.pad(values, ((0, 1), (0, 1), (0, 1)), mode="wrap")


def _marching_indices(values, level, periodic, marching_cubes):
    grid = _closed_values(values, periodic)
    try:
        vertices, faces, _, _ = marching_cubes(grid, level=float(level))
    except (RuntimeError, ValueError) as exc:
        raise ValueError(
            f"marching cubes found no surface at isovalue {float(level):g}"
        ) from exc
    faces = np.asarray(faces)
    if len(faces) == 0:
        raise ValueError(f"marching cubes found no surface at isovalue {float(level):g}")
    return np.asarray(vertices, dtype=float), faces


def _sample_indices(color: np.ndarray, indices: np.ndarray, periodic: bool) -> np.ndarray:
    points = np.asarray(indices, dtype=float)
    if points.size == 0:
        return np.empty(0, dtype=float)
    if periodic:
        extended = _closed_values(color, True)
        wrapped = np.mod(points, np.asarray(color.shape, dtype=float))
        samples = _sample_trilinear(extended, wrapped)
    else:
        samples = _sample_trilinear(color, points)
    if not np.all(np.isfinite(samples)):
        raise ValueError(
            "color grid produced nonfinite samples; nonfinite color values are not mapped"
        )
    return samples


def _sample_world(color, world, origin, axes, periodic) -> np.ndarray:
    indices = (np.asarray(world, dtype=float) - origin) @ np.linalg.inv(axes)
    return _sample_indices(color, indices, periodic)


def _as_pairs(pairs: Sequence | None) -> np.ndarray:
    if pairs is None:
        return np.zeros((0, 2), dtype=int)
    array = np.asarray(pairs)
    if array.size == 0:
        return np.zeros((0, 2), dtype=int)
    if not np.all(np.isfinite(array)):
        raise ValueError("bond indices must be finite integers")
    if not np.all(array == np.floor(array)):
        raise ValueError("bond indices must be integers")
    if array.ndim == 2 and array.shape[1] == 2:
        parsed = array.astype(int)
    elif array.ndim == 1 and array.size % 2 == 0:
        parsed = array.reshape(-1, 2).astype(int)
    else:
        raise ValueError("bonds must be index pairs with shape (N, 2)")
    if np.any(parsed < 0):
        raise ValueError("bond indices must be non-negative")
    return parsed


def _tile_bonds(bonds: np.ndarray, atom_count: int, tiles: int) -> np.ndarray:
    pairs = _as_pairs(bonds)
    if len(pairs) == 0 or tiles == 1:
        return pairs
    return np.vstack([pairs + tile * atom_count for tile in range(tiles)])


def _validate_bond_indices(bonds: np.ndarray, atom_count: int) -> None:
    if len(bonds) == 0:
        return
    if atom_count == 0 or int(bonds.max()) >= atom_count:
        raise ValueError("bond indices are outside the atom list")


def _cell_shifts(lattice: np.ndarray, repeat: tuple[int, int, int]) -> np.ndarray:
    shifts = [
        ia * lattice[0] + ib * lattice[1] + ic * lattice[2]
        for ia in range(repeat[0])
        for ib in range(repeat[1])
        for ic in range(repeat[2])
    ]
    return np.vstack(shifts)


def _repeated_atoms(xyz, atomic_numbers, bonds, lattice, repeat, bond_finder):
    shifts = _cell_shifts(lattice, repeat)
    if len(xyz) == 0:
        draw_xyz = np.zeros((0, 3), dtype=float)
        draw_numbers = np.asarray(atomic_numbers)
    else:
        draw_xyz = np.vstack([xyz + shift for shift in shifts])
        draw_numbers = np.concatenate([np.asarray(atomic_numbers)] * len(shifts))
    if bond_finder is None:
        _validate_bond_indices(bonds, len(xyz))
        draw_bonds = _tile_bonds(bonds, len(xyz), len(shifts))
    else:
        draw_bonds = _as_pairs(bond_finder(draw_xyz, draw_numbers))
        _validate_bond_indices(draw_bonds, len(draw_xyz))
    return draw_xyz, draw_numbers, draw_bonds, shifts


def _repeat_mesh(mesh, shifts: np.ndarray):
    if len(shifts) == 1:
        return mesh
    scalars = np.asarray(mesh.point_data[_SCALAR])
    pieces = []
    for shift in shifts:
        piece = mesh.copy(deep=True)
        piece.points = np.asarray(mesh.points) + shift
        piece.point_data[_SCALAR] = scalars
        pieces.append(piece)
    merged = pieces[0]
    for piece in pieces[1:]:
        merged = merged.merge(piece)
    return merged


def _scene_extent(mesh_points: np.ndarray, xyz: np.ndarray) -> tuple[np.ndarray, float]:
    chunks = []
    if len(mesh_points):
        chunks.append(np.asarray(mesh_points, dtype=float))
    if len(xyz):
        chunks.append(np.asarray(xyz, dtype=float))
    if not chunks:
        raise ValueError("cube figure needs an isosurface or at least one atom")
    stacked = np.vstack(chunks)
    if not np.all(np.isfinite(stacked)):
        raise ValueError("cube figure coordinates must be finite")
    low = stacked.min(axis=0)
    high = stacked.max(axis=0)
    center = 0.5 * (low + high)
    radius = max(0.5 * float(np.linalg.norm(high - low)), _MIN_SCENE_RADIUS)
    return center, radius


def _view_basis(look, up) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    direction = _as_vector(look, "look")
    screen_up = _as_vector(up, "up")
    direction_norm = float(np.linalg.norm(direction))
    up_norm = float(np.linalg.norm(screen_up))
    if direction_norm < 1.0e-12 or up_norm < 1.0e-12:
        raise ValueError("look and up must be non-zero")
    direction = direction / direction_norm
    screen_up = screen_up / up_norm
    side = np.cross(direction, screen_up)
    side_norm = float(np.linalg.norm(side))
    if side_norm < 1.0e-8:
        raise ValueError("look and up must not be parallel")
    side = side / side_norm
    screen_up = np.cross(side, direction)
    return direction, screen_up, side


def _view_rig(center, radius, look, up):
    direction, screen_up, side = _view_basis(look, up)
    distance = float(radius) * _CAMERA_DISTANCE_IN_RADII
    camera = center - direction * distance
    lights = []
    for (along, above, across), intensity in _LIGHTS:
        position = (
            center
            + direction * (distance * along)
            + screen_up * (distance * above)
            + side * (distance * across)
        )
        lights.append((position, float(intensity)))
    near = max(distance - 2.0 * float(radius), distance * 0.05)
    far = distance + 2.0 * float(radius)
    return camera, center, screen_up, lights, (near, far)


def _smooth_taubin(mesh, iterations: int, pass_band: float):
    """Smooth without accepting a pass that collapses the surface.

    ``vtkWindowedSincPolyDataFilter`` can miss its offset on a one-iteration
    pass and return a mesh shrunk toward the origin. Taubin smoothing is not
    supposed to shrink the bounds, so that result is discarded.
    """

    if iterations <= 0:
        return mesh
    before = np.asarray(mesh.points)
    smoothed = mesh.smooth_taubin(
        n_iter=iterations,
        pass_band=pass_band,
        normalize_coordinates=True,
    )
    after = np.asarray(smoothed.points)
    before_span = float(np.linalg.norm(before.max(axis=0) - before.min(axis=0)))
    after_span = float(np.linalg.norm(after.max(axis=0) - after.min(axis=0)))
    if before_span > 0.0 and after_span < 0.5 * before_span:
        return mesh
    return smoothed


def _polydata(pv, vertices, faces):
    cells = np.column_stack([np.full(len(faces), 3), faces]).ravel()
    return pv.PolyData(np.asarray(vertices, dtype=float), cells)


def _as_grid(name: str, values) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 3 or min(array.shape) < 2:
        raise ValueError(f"{name} must be a 3-D grid with at least 2 samples on each axis")
    return array


def _as_vector(value, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite 3-vector")
    return array


def _as_axes(value) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (3, 3) or not np.all(np.isfinite(array)):
        raise ValueError("axes must be a finite 3x3 voxel matrix")
    determinant = float(np.linalg.det(array))
    if not np.isfinite(determinant) or abs(determinant) < _SINGULAR_AXIS_DETERMINANT:
        raise ValueError("voxel axes are singular")
    return array


def _as_atoms(xyz, atomic_numbers) -> tuple[np.ndarray, np.ndarray]:
    points = np.asarray(xyz, dtype=float)
    if points.size == 0:
        points = np.zeros((0, 3), dtype=float)
    elif points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("xyz must have shape (N, 3)")
    if not np.all(np.isfinite(points)):
        raise ValueError("xyz must be finite")
    numbers = np.asarray(atomic_numbers)
    if numbers.shape != (len(points),):
        raise ValueError("atomic_numbers must have one entry per atom")
    if len(numbers) and (
        not np.all(np.isfinite(numbers)) or not np.all(numbers == np.floor(numbers))
    ):
        raise ValueError("atomic_numbers must be integers")
    return points, numbers


def _require_element_style(atomic_numbers, element_color, element_radius) -> None:
    for atomic_number in np.unique(atomic_numbers):
        number = int(atomic_number)
        if number not in element_color or number not in element_radius:
            raise ValueError(
                f"element {number} is missing a caller-supplied color or radius"
            )
        _rgb01(f"element_color[{number}]", element_color[number])
        radius = float(element_radius[number])
        if not np.isfinite(radius) or radius <= 0.0:
            raise ValueError(f"element_radius[{number}] must be finite and positive")


def _rgb01(name: str, value) -> tuple[float, float, float]:
    color = tuple(float(component) for component in value)
    if len(color) != 3 or not all(np.isfinite(color)) or any(
        component < 0.0 or component > 1.0 for component in color
    ):
        raise ValueError(f"{name} must be three finite RGB components in 0..1")
    return color


def _repeat_counts(repeat) -> tuple[int, int, int]:
    values = tuple(repeat)
    if len(values) != 3:
        raise ValueError("repeat must be three positive integers")
    counts = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise TypeError("repeat must be three positive integers")
        number = int(value)
        if number < 1:
            raise ValueError("repeat counts must be >= 1")
        counts.append(number)
    return (counts[0], counts[1], counts[2])


def _positive_int(name: str, value, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer")
    number = int(value)
    if number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return number


def _unit_interval(name: str, value) -> float:
    number = float(value)
    if not np.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must lie in [0, 1]")
    return number


def _isovalue(iso: np.ndarray, isovalue: float | None) -> float:
    if isovalue is None:
        return float(default_isovalue(iso))
    level = float(isovalue)
    if not np.isfinite(level):
        raise ValueError("isovalue must be finite")
    return level


def _require_level_crosses(iso: np.ndarray, level: float) -> None:
    low = float(np.min(iso))
    high = float(np.max(iso))
    if not low < level < high:
        raise ValueError(
            f"isovalue {level:g} does not cross the grid range [{low:g}, {high:g}]"
        )
