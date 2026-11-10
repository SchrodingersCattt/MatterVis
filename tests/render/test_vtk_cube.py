"""PyVista cube figures match a repeated call and the asymmetric colormap."""

from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render.vtk_cube import _colormap, _tile_bonds, render_cube_figure


def test_default_colormap_is_five_stops() -> None:
    assert _colormap(None, -0.04, 0.02).shape == (256, 3)


def test_supplied_bonds_tile_by_atom_count() -> None:
    bonds = np.array([[0, 1], [1, 2]])
    tiled = _tile_bonds(bonds, 4, 2)
    assert np.array_equal(tiled, np.array([[0, 1], [1, 2], [4, 5], [5, 6]]))


def test_asymmetric_green_stop_matches_the_data_zero() -> None:
    stops = [(0, 40, 230), (0, 190, 210), (30, 210, 40), (240, 210, 0), (210, 20, 20)]
    colors = _colormap(stops, -0.04, 0.02)
    zero = (0.0 - (-0.04)) / (0.02 - (-0.04))
    index = int(round(zero * 255))
    assert np.allclose(colors[index], np.array([30, 210, 40]) / 255.0, atol=0.02)


@pytest.mark.filterwarnings(
    "ignore:Setting the shape on a NumPy array:DeprecationWarning"
)
def test_repeated_render_is_identical(tmp_path) -> None:
    pytest.importorskip("pyvista")
    axis = np.linspace(-1.0, 1.0, 12)
    z, y, x = np.meshgrid(axis, axis, axis, indexing="ij")
    rho = np.exp(-(x * x + y * y + z * z) / 0.15)
    origin = np.zeros(3)
    axes = np.eye(3) * (axis[1] - axis[0])
    numbers = np.array([6], dtype=int)
    xyz = np.zeros((1, 3))
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    kwargs = dict(
        isovalue=0.4,
        color_min=-0.2,
        color_max=0.2,
        axis_metric="norm",
        taubin_iterations=2,
        look=np.array([0.0, 0.0, 1.0]),
        up=np.array([0.0, 1.0, 0.0]),
        parallel_scale=1.2,
        width=80,
        height=80,
        element_color={6: (0.2, 0.2, 0.2)},
        element_radius={6: 0.05},
        bonds=np.zeros((0, 2), dtype=int),
        bond_radius=0.02,
        sphere_resolution=8,
        depth_peels=1,
        colormap_stops=[
            (0, 0, 255),
            (0, 128, 255),
            (0, 255, 0),
            (255, 255, 0),
            (255, 0, 0),
        ],
    )
    render_cube_figure(rho, rho - 0.2, origin, axes, numbers, xyz, first, **kwargs)
    render_cube_figure(rho, rho - 0.2, origin, axes, numbers, xyz, second, **kwargs)
    assert first.read_bytes() == second.read_bytes()
