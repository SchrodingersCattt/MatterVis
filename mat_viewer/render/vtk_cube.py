"""Experimental offscreen cube isosurface figures through PyVista.

``render_cube_figure`` is an experimental PNG helper. It is not a
``RenderPlan`` backend and it is not a second scene authority. Pixel
output may differ from the CPU and Plotly backends; this entry does not
claim cross-backend pixel parity. Depth peeling, Taubin smoothing, and
the native window class remain VTK-specific options.

Isosurface shade uses the shared
:class:`~mat_viewer.render.figure_shade.FigureShade` coefficients
(ambient, diffuse, specular, shininess). This module lowers those four
numbers onto a VTK property and maps ``shininess`` to specular power.
It does not choose elements, radii, or bond cutoffs. Positions use the cube
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
enables peeling on the figure window only. Windows and macOS use the native
VTK window class and do not consult ``DISPLAY``.
"""

from __future__ import annotations

import ctypes.util
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import numpy as np

from ._vtk_cube_color import _color_limits, _paint
from ._vtk_cube_color import _colormap as _colormap  # noqa: PLC0414
from ._vtk_cube_geometry import (
    _SCALAR,
    _as_atoms,
    _as_axes,
    _as_grid,
    _as_pairs,
    _as_vector,
    _bond_segments,
    _cell_box,
    _cell_corners,
    _cell_edge_polydata,
    _cell_shifts,  # noqa: F401
    _closed_values,  # noqa: F401
    _isovalue,
    _marching_indices,
    _polydata,
    _positive_int,
    _positive_power,
    _repeat_counts,
    _repeat_mesh,
    _repeated_atoms,
    _require_element_style,
    _require_level_crosses,
    _rgb01,
    _sample_indices,  # noqa: F401
    _sample_world,
    _scene_extent,
    _smooth_taubin,
    _tile_bonds,  # noqa: F401
    _unit_interval,
    _view_rig,
)
from .figure_shade import FigureShade

_BOND_TUBE_SIDES = 8
_SURFACE_SPECULAR = 0.08
_SURFACE_SPECULAR_POWER = 15
_SURFACE_DIFFUSE = 0.45
_SURFACE_AMBIENT = 0.7


class _ShadeDefault:
    """Keyword default distinct from an explicit shade coefficient.

    ``surface_*`` arguments stay at the historical numbers when omitted.
    The wrapper keeps that omission visible, so passing ``surface`` together
    with any of those arguments can raise without shifting the default PNG.
    """

    __slots__ = ("value",)

    def __init__(self, value: float) -> None:
        self.value = value

    def __repr__(self) -> str:
        return repr(self.value)


_DEFAULT_SURFACE_AMBIENT = _ShadeDefault(_SURFACE_AMBIENT)
_DEFAULT_SURFACE_DIFFUSE = _ShadeDefault(_SURFACE_DIFFUSE)
_DEFAULT_SURFACE_SPECULAR = _ShadeDefault(_SURFACE_SPECULAR)
_DEFAULT_SURFACE_SPECULAR_POWER = _ShadeDefault(_SURFACE_SPECULAR_POWER)
_BOND_SPECULAR = 0.05
_BOND_AMBIENT = 0.2
_BOND_DIFFUSE = 0.8
_ATOM_SPECULAR = 0.35
_ATOM_SPECULAR_POWER = 20
_ATOM_AMBIENT = 0.85
_ATOM_DIFFUSE = 0.4
_RENDERER_AMBIENT = (1.0, 1.0, 1.0)
# Desktop platforms render offscreen through the native VTK window. They do
# not set DISPLAY, and a missing OSMesa/EGL library is not a failure there.
_NATIVE_OPENGL_WINDOW = {
    "win32": "vtkWin32OpenGLRenderWindow",
    "darwin": "vtkCocoaOpenGLRenderWindow",
}


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
    surface: FigureShade | Mapping | None = None,
    surface_ambient: float | _ShadeDefault = _DEFAULT_SURFACE_AMBIENT,
    surface_diffuse: float | _ShadeDefault = _DEFAULT_SURFACE_DIFFUSE,
    surface_specular: float | _ShadeDefault = _DEFAULT_SURFACE_SPECULAR,
    surface_specular_power: float | _ShadeDefault = _DEFAULT_SURFACE_SPECULAR_POWER,
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
    this window. Pass ``surface`` as a :class:`~mat_viewer.render.figure_shade.FigureShade`
    or a mapping of ``ambient``, ``diffuse``, ``specular``, and ``shininess``
    to shade the isosurface. ``shininess`` is VTK's specular power, the Phong
    exponent. Omit ``surface`` to use ``surface_ambient``, ``surface_diffuse``,
    ``surface_specular``, and ``surface_specular_power``. Those arguments are
    the explicit shade only when ``surface`` is omitted, and their defaults
    stay 0.7, 0.45, 0.08, and 15. Passing ``surface`` together with any of
    them raises ``ValueError``. Atom and bond coefficients are separate.
    Pass ``cell_origin`` and ``cell_vectors`` (three lattice rows) to draw
    cell edges. ``cell_edge_mode="box"`` draws all twelve edges;
    ``"face"`` draws the face whose outward normal points toward the camera,
    so opposite ``look`` directions select opposite faces.
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
    surface_shade = _resolve_surface_shade(
        surface,
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







_SURFACE_FIELDS = ("ambient", "diffuse", "specular", "shininess")


def _shade(name, ambient, diffuse, specular, power) -> tuple[float, float, float, float]:
    return (
        _unit_interval(f"{name}_ambient", ambient),
        _unit_interval(f"{name}_diffuse", diffuse),
        _unit_interval(f"{name}_specular", specular),
        _positive_power(f"{name}_specular_power", power),
    )


def _shade_number(value: float | _ShadeDefault) -> float:
    if isinstance(value, _ShadeDefault):
        return value.value
    return value


def _figure_shade(surface: FigureShade | Mapping) -> FigureShade:
    if isinstance(surface, FigureShade):
        return surface
    if isinstance(surface, Mapping):
        if set(surface) != set(_SURFACE_FIELDS):
            raise ValueError(
                "surface mapping must provide ambient, diffuse, specular, "
                "and shininess"
            )
        return FigureShade(
            ambient=surface["ambient"],
            diffuse=surface["diffuse"],
            specular=surface["specular"],
            shininess=surface["shininess"],
        )
    raise TypeError(
        "surface must be a FigureShade or a mapping of ambient, diffuse, "
        "specular, and shininess"
    )


def _resolve_surface_shade(
    surface: FigureShade | Mapping | None = None,
    surface_ambient: float | _ShadeDefault = _DEFAULT_SURFACE_AMBIENT,
    surface_diffuse: float | _ShadeDefault = _DEFAULT_SURFACE_DIFFUSE,
    surface_specular: float | _ShadeDefault = _DEFAULT_SURFACE_SPECULAR,
    surface_specular_power: float | _ShadeDefault = _DEFAULT_SURFACE_SPECULAR_POWER,
) -> tuple[float, float, float, float]:
    """Return ambient, diffuse, specular, and shininess for the isosurface.

    The fourth number is VTK specular power. ``surface`` supplies all four
    when it is present. The legacy ``surface_*`` arguments supply them only
    when ``surface`` is omitted.
    """

    provided = [
        name
        for name, value in (
            ("surface_ambient", surface_ambient),
            ("surface_diffuse", surface_diffuse),
            ("surface_specular", surface_specular),
            ("surface_specular_power", surface_specular_power),
        )
        if not isinstance(value, _ShadeDefault)
    ]
    if surface is not None and provided:
        names = ", ".join(provided)
        raise ValueError(
            "pass surface or the surface_* shade arguments, not both "
            f"({names})"
        )
    if surface is not None:
        shade = _figure_shade(surface)
        return (shade.ambient, shade.diffuse, shade.specular, shade.shininess)
    return _shade(
        "surface",
        _shade_number(surface_ambient),
        _shade_number(surface_diffuse),
        _shade_number(surface_specular),
        _shade_number(surface_specular_power),
    )
















def _shared_library(name: str) -> str | None:
    return ctypes.util.find_library(name)


def _prepare_vtk_window() -> None:
    """Select an offscreen window class before VTK creates one.

    A headless Linux process with no OSMesa or EGL library segfaults inside
    ``vtkRenderWindow.Render``. The failure cannot be caught, and a thread
    timeout cannot interrupt it. Windows and macOS do not set ``DISPLAY``;
    their native VTK window classes render offscreen without OSMesa or EGL.
    """

    if os.environ.get("VTK_DEFAULT_OPENGL_WINDOW"):
        return
    native = _NATIVE_OPENGL_WINDOW.get(sys.platform)
    if native is not None:
        os.environ["VTK_DEFAULT_OPENGL_WINDOW"] = native
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
        "(OSMesa or EGL) on Linux. Windows and macOS use the native VTK "
        "window. On headless Linux install the OSMesa package "
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
        cell_points = _cell_corners(*cell_box)
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















__all__ = ["render_cube_figure"]
