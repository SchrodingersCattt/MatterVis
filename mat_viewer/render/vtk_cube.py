"""Offscreen cube isosurface figures through PyVista.

The CPU rasterizer does not reproduce a VTK depth-peeled render. This
module is the optional path that does: marching cubes, optional Taubin
smoothing, an orthographic camera, and a three-point light rig in the
camera basis. PyVista is imported only when the function is called.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

# Camera and lights are fixed in the view basis: look points from the camera
# toward the focal point, up is the screen-up axis, and side is their cross
# product. Offsets are (along look, along up, along side) in angstroms.
_CAMERA_DISTANCE = 80.0
_LIGHTS = (
    ((40.0, 0.0, 0.0), 1.0),
    ((20.0, 30.0, 10.0), 0.7),
    ((15.0, -20.0, -20.0), 0.45),
)
_DEFAULT_STOPS = (
    (0, 0, 255),
    (0, 128, 255),
    (0, 255, 0),
    (255, 255, 0),
    (255, 0, 0),
)


def render_cube_figure(
    iso,
    color,
    origin,
    axes,
    atomic_numbers,
    xyz,
    output: str | Path,
    *,
    isovalue: float = 1.0,
    color_min: float = -0.04,
    color_max: float = 0.02,
    opacity: float = 0.72,
    axis_metric: str = "norm",
    periodic: bool = False,
    taubin_iterations: int = 20,
    taubin_pass_band: float = 0.1,
    repeat: tuple[int, int, int] = (1, 1, 1),
    look: np.ndarray,
    up: np.ndarray,
    parallel_scale: float,
    width: int,
    height: int,
    element_color: dict[int, tuple[float, float, float]],
    element_radius: dict[int, float],
    bonds: np.ndarray,
    bond_finder: Callable[[np.ndarray, np.ndarray], Sequence] | None = None,
    bond_radius: float,
    bond_color: tuple[float, float, float] = (0.45, 0.45, 0.45),
    sphere_resolution: int = 18,
    depth_peels: int = 8,
    colormap_stops: list[tuple[int, int, int]] | None = None,
) -> None:
    """Write one PNG. ``look`` points from the camera toward the focal point.

    ``bonds`` are index pairs in the unrepeated cell. They are translated
    with each repeated cell. ``bond_finder(xyz, atomic_numbers)`` replaces
    that list and is called on the coordinates after the repeat, so the
    caller can include contacts that cross a cell boundary. This function
    does not choose elements or distance cutoffs.
    """

    try:
        import pyvista as pv
    except ImportError as exc:
        raise ImportError(
            "render_cube_figure requires PyVista; install the 'vtk' extra"
        ) from exc
    from matplotlib.colors import ListedColormap
    from scipy.interpolate import RegularGridInterpolator
    from skimage.measure import marching_cubes

    if axis_metric not in {"norm", "matrix"}:
        raise ValueError("axis_metric must be 'norm' or 'matrix'")
    mesh = _isosurface(
        np.asarray(iso, dtype=float),
        np.asarray(color, dtype=float),
        np.asarray(origin, dtype=float),
        np.asarray(axes, dtype=float),
        float(isovalue),
        float(color_min),
        float(color_max),
        axis_metric,
        periodic,
        marching_cubes,
        RegularGridInterpolator,
        pv,
    )
    mesh = mesh.smooth_taubin(
        n_iter=int(taubin_iterations), pass_band=float(taubin_pass_band)
    )
    mesh["sign_l2_rho"] = np.clip(
        _sample(
            np.asarray(color, dtype=float),
            np.asarray(origin, dtype=float),
            np.asarray(axes, dtype=float),
            np.asarray(mesh.points),
            axis_metric,
            RegularGridInterpolator,
        ),
        color_min,
        color_max,
    )
    mesh = mesh.compute_normals(auto_orient_normals=True)
    draw_xyz, draw_z, draw_bonds, draw_mesh = _repeat(
        np.asarray(xyz, dtype=float),
        np.asarray(atomic_numbers),
        np.asarray(bonds, dtype=int),
        mesh,
        np.asarray(axes, dtype=float) * np.array(np.asarray(iso).shape)[:, None],
        repeat,
        pv,
        bond_finder,
    )
    _draw(
        pv,
        draw_mesh,
        draw_xyz,
        draw_z,
        draw_bonds,
        ListedColormap(_colormap(colormap_stops, color_min, color_max)),
        float(color_min),
        float(color_max),
        float(opacity),
        np.asarray(look, dtype=float),
        np.asarray(up, dtype=float),
        float(parallel_scale),
        int(width),
        int(height),
        element_color,
        element_radius,
        float(bond_radius),
        bond_color,
        int(sphere_resolution),
        int(depth_peels),
        Path(output),
    )


def _colormap(stops: list[tuple[int, int, int]] | None, color_min: float, color_max: float) -> np.ndarray:
    control = list(_DEFAULT_STOPS if stops is None else stops)
    if len(control) != 5:
        raise ValueError("colormap_stops must contain 5 RGB tuples")
    zero = (0.0 - float(color_min)) / (float(color_max) - float(color_min))
    nodes = [np.array(item) for item in control]

    def mix(left, right, fraction):
        return left + fraction * (right - left)

    colors = np.zeros((256, 3))
    for index in range(256):
        t = index / 255.0
        if t <= zero:
            fraction = t / zero if zero else 0.0
            colors[index] = (
                mix(nodes[0], nodes[1], fraction / 0.5)
                if fraction < 0.5
                else mix(nodes[1], nodes[2], (fraction - 0.5) / 0.5)
            )
        else:
            fraction = (t - zero) / (1.0 - zero)
            colors[index] = (
                mix(nodes[2], nodes[3], fraction / 0.5)
                if fraction < 0.5
                else mix(nodes[3], nodes[4], (fraction - 0.5) / 0.5)
            )
    return colors / 255.0


def _isosurface(iso, color, origin, axes, level, cmin, cmax, metric, periodic, marching_cubes, interpolator, pv):
    if periodic:
        padded = np.pad(iso, 1, mode="wrap")
        vertices, faces, _, _ = marching_cubes(padded, level=level)
        vertices = vertices - 1.0
        shape = np.array(iso.shape, dtype=float)
        keep = np.all(
            (vertices[faces].mean(axis=1) >= 0.0)
            & (vertices[faces].mean(axis=1) < shape),
            axis=1,
        )
        faces = faces[keep]
        vertices = origin + vertices @ axes
    elif metric == "norm":
        spacing = tuple(float(np.linalg.norm(axes[i])) for i in range(3))
        vertices, faces, _, _ = marching_cubes(iso, level=level, spacing=spacing)
        vertices = vertices + origin
    else:
        vertices, faces, _, _ = marching_cubes(iso, level=level)
        vertices = origin + vertices @ axes
    mesh = pv.PolyData(vertices, np.column_stack([np.full(len(faces), 3), faces]).ravel())
    mesh["sign_l2_rho"] = np.clip(
        _sample(color, origin, axes, vertices, metric, interpolator), cmin, cmax
    )
    return mesh


def _sample(color, origin, axes, points, metric, interpolator):
    if metric == "norm" and points is not None:
        grid = tuple(origin[i] + np.arange(color.shape[i]) * axes[i, i] for i in range(3))
        sampler = interpolator(grid, color, bounds_error=False, fill_value=0.0)
        return sampler(points)
    shape = np.array(color.shape, dtype=float)
    grid_x = origin[0] + np.arange(color.shape[0]) * axes[0, 0]
    sampler = interpolator(
        (grid_x, np.arange(color.shape[1]), np.arange(color.shape[2])),
        color,
        bounds_error=False,
        fill_value=0.0,
    )
    index = np.mod((np.asarray(points) - origin) @ np.linalg.inv(axes), shape)
    samples = np.column_stack(
        [origin[0] + index[:, 0] * axes[0, 0], index[:, 1], index[:, 2]]
    )
    return sampler(samples)


def _as_pairs(pairs: Sequence) -> np.ndarray:
    array = np.asarray(pairs, dtype=int)
    if array.size == 0:
        return np.zeros((0, 2), dtype=int)
    return array.reshape(-1, 2)


def _tile_bonds(bonds: np.ndarray, atom_count: int, tiles: int) -> np.ndarray:
    pairs = _as_pairs(bonds)
    if len(pairs) == 0 or tiles == 1:
        return pairs
    return np.vstack([pairs + tile * atom_count for tile in range(tiles)])


def _repeat(xyz, atomic_numbers, bonds, mesh, lattice, repeat, pv, bond_finder):
    if repeat == (1, 1, 1):
        if bond_finder is not None:
            bonds = _as_pairs(bond_finder(xyz, atomic_numbers))
        return xyz, atomic_numbers, bonds, mesh
    pieces = []
    blocks_xyz = []
    blocks_z = []
    for ia in range(repeat[0]):
        for ib in range(repeat[1]):
            for ic in range(repeat[2]):
                shift = ia * lattice[0] + ib * lattice[1] + ic * lattice[2]
                piece = mesh.copy(deep=True)
                piece.points = np.asarray(mesh.points) + shift
                piece["sign_l2_rho"] = np.asarray(mesh["sign_l2_rho"])
                pieces.append(piece)
                blocks_xyz.append(xyz + shift)
                blocks_z.append(atomic_numbers)
    merged = pieces[0]
    for piece in pieces[1:]:
        merged = merged.merge(piece)
    draw_xyz = np.vstack(blocks_xyz)
    draw_z = np.concatenate(blocks_z)
    if bond_finder is None:
        draw_bonds = _tile_bonds(bonds, len(xyz), len(blocks_xyz))
    else:
        draw_bonds = _as_pairs(bond_finder(draw_xyz, draw_z))
    return draw_xyz, draw_z, draw_bonds, merged


def _draw(
    pv, mesh, xyz, atomic_numbers, bonds, cmap, cmin, cmax, opacity,
    look, up, parallel_scale, width, height, element_color, element_radius,
    bond_radius, bond_color, sphere_resolution, depth_peels, output,
):
    pv.OFF_SCREEN = True
    plotter = pv.Plotter(off_screen=True, window_size=[width, height])
    plotter.set_background("white")
    plotter.renderer.SetAmbient(1.0, 1.0, 1.0)
    plotter.enable_depth_peeling(number_of_peels=depth_peels)
    plotter.add_mesh(
        mesh,
        scalars="sign_l2_rho",
        cmap=cmap,
        clim=[cmin, cmax],
        opacity=opacity,
        smooth_shading=True,
        specular=0.08,
        specular_power=15,
        diffuse=0.45,
        ambient=0.7,
        show_scalar_bar=False,
    )
    if len(bonds):
        points, lines, cursor = [], [], 0
        for i, j in bonds:
            points.extend([xyz[i], xyz[j]])
            lines.append([2, cursor, cursor + 1])
            cursor += 2
        tubes = pv.PolyData(np.array(points), lines=np.hstack(lines)).tube(
            radius=bond_radius, n_sides=8
        )
        plotter.add_mesh(
            tubes, color=bond_color, specular=0.05, ambient=0.2, diffuse=0.8
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
            specular=0.35,
            specular_power=20,
            ambient=0.85,
            diffuse=0.4,
        )
    focal = xyz.mean(axis=0)
    plotter.camera_position = [
        tuple(focal - look * _CAMERA_DISTANCE),
        tuple(focal),
        tuple(up),
    ]
    plotter.camera.parallel_projection = True
    plotter.camera.parallel_scale = parallel_scale
    plotter.remove_all_lights()
    side = np.cross(look, up)
    for (along, above, across), intensity in _LIGHTS:
        position = focal + look * along + up * above + side * across
        plotter.add_light(
            pv.Light(
                position=tuple(position),
                focal_point=tuple(focal),
                intensity=intensity,
            )
        )
    plotter.screenshot(str(output))
    plotter.close()
