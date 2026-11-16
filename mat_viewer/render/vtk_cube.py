"""Offscreen cube isosurface figures through PyVista.

This is one optional PNG entry. It is not a ``RenderPlan`` backend and it
does not choose elements, radii, or bond cutoffs. Positions use the cube
frame ``origin + index @ axes`` (voxel vectors are rows), the same frame as
:class:`mat_viewer.cube.io.CubeData`. The color grid shares that origin,
those axes, and that shape.

The orthographic distance and the three lights scale with the scene radius.
A fixed angstrom rig leaves the camera and the lights inside a large cell.
Color limits default to the finite range of the color grid. ``color_center``
is optional; zero is not pinned unless the caller asks.

``periodic=True`` appends the wrapped endpoint plane (index ``N`` copies
index 0) so the surface can reach the cell faces. It does not drop faces
outside the original index box. Taubin smoothing is Cartesian and can pull
those faces off the cell; colors are sampled after the vertices move.
Non-periodic samples that leave the grid are clamped. Periodic samples wrap.
They are not taken from the opposite side unless ``periodic`` is true.

PyVista's depth-peeling probe opens a second window and aborted the
interpreter on headless Linux when no display, OSMesa, or EGL library was
available. This module selects a window class before creating one, and it
enables peeling on the figure window only.
"""

from __future__ import annotations

import ctypes.util
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np

from ..cube.color_field import (
    _SINGULAR_AXIS_DETERMINANT,
    _sample_trilinear,
)
from ..cube.io import default_isovalue
from ._vtk_cube_color import _colormap as _colormap
from ._vtk_cube_color import _color_limits, _paint

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
_BOND_TUBE_SIDES = 8
_SURFACE_SPECULAR = 0.08
_SURFACE_SPECULAR_POWER = 15
_SURFACE_DIFFUSE = 0.45
_SURFACE_AMBIENT = 0.7
_BOND_SPECULAR = 0.05
_BOND_AMBIENT = 0.2
_BOND_DIFFUSE = 0.8
_ATOM_SPECULAR = 0.35
_ATOM_SPECULAR_POWER = 20
_ATOM_AMBIENT = 0.85
_ATOM_DIFFUSE = 0.4
_RENDERER_AMBIENT = (1.0, 1.0, 1.0)


def render_cube_figure(
    iso,
    color,
    origin,
    axes,
    atomic_numbers,
    xyz,
    output: str | Path,
    *,
    look: np.ndarray,
    up: np.ndarray,
    parallel_scale: float,
    width: int,
    height: int,
    element_color: Mapping[int, Sequence[float]],
    element_radius: Mapping[int, float],
    isovalue: float | None = None,
    color_min: float | None = None,
    color_max: float | None = None,
    color_center: float | None = None,
    opacity: float = 0.72,
    periodic: bool = False,
    taubin_iterations: int = 20,
    taubin_pass_band: float = 0.1,
    repeat: tuple[int, int, int] = (1, 1, 1),
    bonds: np.ndarray | None = None,
    bond_finder: Callable[[np.ndarray, np.ndarray], Sequence] | None = None,
    bond_radius: float = 0.15,
    bond_color: tuple[float, float, float] | None = (0.45, 0.45, 0.45),
    sphere_resolution: int = 18,
    depth_peels: int = 8,
    colormap_stops: Sequence[tuple[int, int, int]] | None = None,
    surface_ambient: float = _SURFACE_AMBIENT,
    surface_diffuse: float = _SURFACE_DIFFUSE,
    surface_specular: float = _SURFACE_SPECULAR,
    surface_specular_power: float = _SURFACE_SPECULAR_POWER,
    atom_ambient: float = _ATOM_AMBIENT,
    atom_diffuse: float = _ATOM_DIFFUSE,
    atom_specular: float = _ATOM_SPECULAR,
    atom_specular_power: float = _ATOM_SPECULAR_POWER,
    bond_ambient: float = _BOND_AMBIENT,
    bond_diffuse: float = _BOND_DIFFUSE,
    bond_specular: float = _BOND_SPECULAR,
    cell_origin: np.ndarray | None = None,
    cell_vectors: np.ndarray | None = None,
    cell_edge_color: tuple[float, float, float] = (0.15, 0.15, 0.15),
    cell_edge_width: float = 2.0,
    cell_edge_mode: str = "box",
) -> None:
    """Write one PNG. ``look`` points from the camera toward the scene.

    ``bonds`` are index pairs in the unrepeated cell. A repeat translates
    those pairs with each cell. ``bond_finder(xyz, atomic_numbers)`` replaces
    that list and is called on the coordinates after the repeat, so the
    caller can include contacts that cross a cell boundary. This function
    does not choose elements or distance cutoffs.

    Omit ``color_min`` and ``color_max`` to use the finite range of ``color``.
    Pass ``color_center`` (often 0) to place that value at the middle of the
    colormap. ``parallel_scale`` is the orthographic half-height in the same
    length unit as ``axes``. ``depth_peels`` above 1 enables depth peeling on
    this window. ``surface_ambient``, ``surface_diffuse``, ``surface_specular``,
    and ``surface_specular_power`` shade the isosurface. Atom and bond
    coefficients are separate. VTK's specular power is the Phong exponent.
    Pass ``cell_origin`` and ``cell_vectors`` (three lattice rows) to draw
    cell edges. ``cell_edge_mode="box"`` draws all twelve edges;
    ``"face"`` draws only the face most facing the camera (no depth edges).
    Omit both cell arguments to leave the box off. ``bond_color=None``
    draws each half of a bond in the endpoint atom color; a single RGB triple
    keeps a uniform stick.
    """

    iso_grid = _as_grid("iso", iso)
    color_grid = _as_grid("color", color)
    if iso_grid.shape != color_grid.shape:
        raise ValueError(
            "iso and color must share one shape; resample a second grid onto "
            "these voxel axes before calling render_cube_figure"
        )
    if not np.all(np.isfinite(iso_grid)):
        raise ValueError("isosurface grid must be finite")
    origin_vector = _as_vector(origin, "origin")
    axis_matrix = _as_axes(axes)
    counts = _repeat_counts(repeat)
    points, numbers = _as_atoms(xyz, atomic_numbers)
    _require_element_style(numbers, element_color, element_radius)
    level = _isovalue(iso_grid, isovalue)
    _require_level_crosses(iso_grid, level)
    opacity_value = _unit_interval("opacity", opacity)
    iterations = _positive_int("taubin_iterations", taubin_iterations, minimum=0)
    if iterations > 0 and not 0.0 < float(taubin_pass_band) <= 2.0:
        raise ValueError("taubin_pass_band must lie in (0, 2]")
    radius = float(bond_radius)
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError("bond_radius must be finite and positive")
    bond_rgb = None if bond_color is None else _rgb01("bond_color", bond_color)
    surface_shade = _shade(
        "surface",
        surface_ambient,
        surface_diffuse,
        surface_specular,
        surface_specular_power,
    )
    atom_shade = _shade(
        "atom", atom_ambient, atom_diffuse, atom_specular, atom_specular_power
    )
    bond_shade = (
        _unit_interval("bond_ambient", bond_ambient),
        _unit_interval("bond_diffuse", bond_diffuse),
        _unit_interval("bond_specular", bond_specular),
    )
    cell_box = _cell_box(cell_origin, cell_vectors)
    cell_rgb = _rgb01("cell_edge_color", cell_edge_color)
    edge_width = float(cell_edge_width)
    if not np.isfinite(edge_width) or edge_width <= 0.0:
        raise ValueError("cell_edge_width must be finite and positive")
    edge_mode = str(cell_edge_mode).strip().lower()
    if edge_mode not in {"box", "face"}:
        raise ValueError("cell_edge_mode must be 'box' or 'face'")
    resolution = _positive_int("sphere_resolution", sphere_resolution, minimum=3)
    peels = _positive_int("depth_peels", depth_peels, minimum=1)
    frame_scale = float(parallel_scale)
    if not np.isfinite(frame_scale) or frame_scale <= 0.0:
        raise ValueError("parallel_scale must be finite and positive")
    pixel_size = (
        _positive_int("width", width, minimum=1),
        _positive_int("height", height, minimum=1),
    )
    limits = _color_limits(color_grid, color_min, color_max)
    if limits is None and color_center is not None:
        raise ValueError(
            "color_center requires a color range, and the color grid is constant"
        )
    destination = Path(output)
    if not destination.parent.exists():
        raise FileNotFoundError(f"output directory does not exist: {destination.parent}")

    _prepare_vtk_window()
    try:
        import pyvista as pv
    except ImportError as exc:
        raise ImportError(
            "render_cube_figure requires PyVista; install the 'vtk' extra"
        ) from exc
    try:
        from skimage.measure import marching_cubes
    except ImportError as exc:
        raise ImportError(
            "render_cube_figure requires scikit-image; install the 'vtk' extra"
        ) from exc

    vertices, faces = _marching_indices(iso_grid, level, periodic, marching_cubes)
    world = origin_vector + vertices @ axis_matrix
    mesh = _polydata(pv, world, faces)
    mesh = _smooth_taubin(mesh, iterations, float(taubin_pass_band))
    samples = _sample_world(
        color_grid,
        np.asarray(mesh.points),
        origin_vector,
        axis_matrix,
        periodic,
    )
    scalars, clim, lut = _paint(samples, limits, color_center, colormap_stops)
    mesh = mesh.compute_normals(auto_orient_normals=True)
    mesh.point_data[_SCALAR] = scalars
    lattice = axis_matrix * np.asarray(iso_grid.shape, dtype=float)[:, None]
    draw_xyz, draw_numbers, draw_bonds, shifts = _repeated_atoms(
        points,
        numbers,
        _as_pairs(bonds),
        lattice,
        counts,
        bond_finder,
    )
    draw_mesh = _repeat_mesh(mesh, shifts)
    _draw(
        pv,
        draw_mesh,
        draw_xyz,
        draw_numbers,
        draw_bonds,
        lut,
        clim,
        opacity_value,
        look,
        up,
        frame_scale,
        pixel_size,
        element_color,
        element_radius,
        radius,
        bond_rgb,
        resolution,
        peels,
        destination,
        surface_shade,
        atom_shade,
        bond_shade,
        cell_box,
        cell_rgb,
        edge_width,
        edge_mode,
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


def _cell_edge_polydata(
    pv,
    origin: np.ndarray,
    vectors: np.ndarray,
    *,
    look: np.ndarray | None = None,
    mode: str = "box",
):
    corners = np.array(
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
        direction = direction / np.linalg.norm(direction)
        # Six faces as (corner index quad, edge-pair axes into the face).
        faces = (
            (0, 1, 4, 2),  # constant 0 along c*? no: o,a,a+b,b  (no c)
            (3, 5, 7, 6),  # +c
            (0, 1, 5, 3),  # o,a,a+c,c (no b)
            (2, 4, 7, 6),  # +b
            (0, 2, 6, 3),  # o,b,b+c,c (no a)
            (1, 4, 7, 5),  # +a
        )
        best_i = 0
        best_dot = -1.0
        for index, face in enumerate(faces):
            u = corners[face[1]] - corners[face[0]]
            v = corners[face[3]] - corners[face[0]]
            normal = np.cross(u, v)
            norm = float(np.linalg.norm(normal))
            if norm < 1.0e-12:
                continue
            normal = normal / norm
            # Face whose normal aligns with the view (either side).
            score = abs(float(np.dot(normal, direction)))
            if score > best_dot:
                best_dot = score
                best_i = index
        face = faces[best_i]
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


def _shade(name, ambient, diffuse, specular, power) -> tuple[float, float, float, float]:
    return (
        _unit_interval(f"{name}_ambient", ambient),
        _unit_interval(f"{name}_diffuse", diffuse),
        _unit_interval(f"{name}_specular", specular),
        _positive_power(f"{name}_specular_power", power),
    )


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


def _shared_library(name: str) -> str | None:
    return ctypes.util.find_library(name)


def _prepare_vtk_window() -> None:
    """Select an offscreen window class before VTK creates one.

    A headless process with no OSMesa or EGL library segfaults inside
    ``vtkRenderWindow.Render``. The failure cannot be caught, and a thread
    timeout cannot interrupt it.
    """

    if os.environ.get("VTK_DEFAULT_OPENGL_WINDOW"):
        return
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    if _shared_library("OSMesa"):
        os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkOSOpenGLRenderWindow"
        return
    if _shared_library("EGL"):
        os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = "vtkEGLRenderWindow"
        return
    raise RuntimeError(
        "render_cube_figure needs a display or an offscreen OpenGL library "
        "(OSMesa or EGL). On headless Linux install the OSMesa package "
        "(Debian/Ubuntu: libosmesa6). MatterVis does not install system "
        "libraries. Set VTK_DEFAULT_OPENGL_WINDOW to choose a window class "
        "this VTK build provides."
    )


def _apply_depth_peeling(plotter, peels: int) -> None:
    """Enable peeling on this figure's window.

    ``Plotter.enable_depth_peeling`` renders a second window to probe support.
    That probe is the call that aborted the CI interpreter.
    """

    if peels <= 1:
        return
    window = plotter.render_window
    window.SetAlphaBitPlanes(True)
    window.SetMultiSamples(0)
    renderer = plotter.renderer
    renderer.SetUseDepthPeeling(True)
    renderer.SetMaximumNumberOfPeels(int(peels))
    renderer.SetOcclusionRatio(0.0)


def _draw(
    pv,
    mesh,
    xyz,
    atomic_numbers,
    bonds,
    lut,
    clim,
    opacity,
    look,
    up,
    parallel_scale,
    pixel_size,
    element_color,
    element_radius,
    bond_radius,
    bond_color,
    sphere_resolution,
    depth_peels,
    output,
    surface_shade,
    atom_shade,
    bond_shade,
    cell_box,
    cell_edge_color,
    cell_edge_width,
    cell_edge_mode,
) -> None:
    from matplotlib.colors import ListedColormap

    cell_points = np.empty((0, 3))
    if cell_box is not None:
        corner, lattice = cell_box
        cell_points = np.array(
            [
                corner,
                corner + lattice[0],
                corner + lattice[1],
                corner + lattice[2],
                corner + lattice[0] + lattice[1],
                corner + lattice[0] + lattice[2],
                corner + lattice[1] + lattice[2],
                corner + lattice[0] + lattice[1] + lattice[2],
            ],
            dtype=float,
        )
    center, radius = _scene_extent(
        np.vstack([np.asarray(mesh.points), cell_points])
        if len(cell_points)
        else np.asarray(mesh.points),
        xyz,
    )
    camera, focal, screen_up, lights, clipping = _view_rig(center, radius, look, up)
    # Do not assign pv.OFF_SCREEN. The plotter flag is local; the global
    # switches later plots in this process to offscreen mode.
    plotter = pv.Plotter(
        off_screen=True,
        window_size=list(pixel_size),
        lighting="none",
    )
    try:
        plotter.set_background("white")
        plotter.renderer.SetAmbient(*_RENDERER_AMBIENT)
        plotter.renderer.SetTwoSidedLighting(True)
        _apply_depth_peeling(plotter, depth_peels)
        plotter.add_mesh(
            mesh,
            scalars=_SCALAR,
            cmap=ListedColormap(lut),
            clim=list(clim),
            opacity=opacity,
            smooth_shading=True,
            ambient=surface_shade[0],
            diffuse=surface_shade[1],
            specular=surface_shade[2],
            specular_power=surface_shade[3],
            show_scalar_bar=False,
        )
        if len(bonds):
            for color, segments in _bond_segments(
                xyz,
                atomic_numbers,
                bonds,
                element_color,
                bond_color,
                element_radius,
            ).items():
                tubes = pv.PolyData(
                    np.asarray(segments["points"], dtype=float),
                    lines=np.hstack(segments["lines"]),
                ).tube(radius=bond_radius, n_sides=_BOND_TUBE_SIDES)
                plotter.add_mesh(
                    tubes,
                    color=color,
                    smooth_shading=True,
                    ambient=bond_shade[0],
                    diffuse=bond_shade[1],
                    specular=bond_shade[2],
                )
        for atomic_number in np.unique(atomic_numbers):
            number = int(atomic_number)
            mask = atomic_numbers == atomic_number
            cloud = pv.PolyData(xyz[mask])
            sphere = pv.Sphere(
                radius=float(element_radius[number]),
                theta_resolution=sphere_resolution,
                phi_resolution=sphere_resolution,
            )
            plotter.add_mesh(
                cloud.glyph(geom=sphere, orient=False, scale=False),
                color=element_color[number],
                smooth_shading=True,
                ambient=atom_shade[0],
                diffuse=atom_shade[1],
                specular=atom_shade[2],
                specular_power=atom_shade[3],
            )
        if cell_box is not None:
            plotter.add_mesh(
                _cell_edge_polydata(
                    pv,
                    cell_box[0],
                    cell_box[1],
                    look=look,
                    mode=cell_edge_mode,
                ),
                color=cell_edge_color,
                line_width=cell_edge_width,
                ambient=1.0,
                diffuse=0.0,
                specular=0.0,
            )
        plotter.camera_position = [tuple(camera), tuple(focal), tuple(screen_up)]
        plotter.camera.parallel_projection = True
        plotter.camera.parallel_scale = parallel_scale
        plotter.camera.clipping_range = clipping
        plotter.remove_all_lights()
        for position, intensity in lights:
            plotter.add_light(
                pv.Light(
                    position=tuple(position),
                    focal_point=tuple(focal),
                    intensity=intensity,
                    positional=False,
                )
            )
        plotter.screenshot(str(output))
    finally:
        plotter.close()


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


__all__ = ["render_cube_figure"]
