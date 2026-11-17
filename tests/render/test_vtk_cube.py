"""PyVista cube figures use the cube frame and do not abort headless runs."""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mat_viewer.render import vtk_cube
from mat_viewer.render.figure_shade import FigureShade
from mat_viewer.render.vtk_cube import (
    _SCALAR,
    _apply_depth_peeling,
    _bond_segments,
    _cell_edge_polydata,
    _cell_shifts,
    _closed_values,
    _color_limits,
    _colormap,
    _marching_indices,
    _prepare_vtk_window,
    _repeat_mesh,
    _repeated_atoms,
    _resolve_surface_shade,
    _sample_indices,
    _sample_world,
    _scene_extent,
    _smooth_taubin,
    _tile_bonds,
    _view_rig,
    render_cube_figure,
)

ROOT = Path(__file__).resolve().parents[2]


def test_default_colormap_is_five_stops() -> None:
    colors = _colormap(None, -0.2, 0.8)
    assert colors.shape == (256, 3)
    assert np.allclose(colors[0], np.array([0, 0, 255]) / 255.0)
    assert np.allclose(colors[-1], np.array([255, 0, 0]) / 255.0)


def test_supplied_bonds_tile_by_atom_count() -> None:
    bonds = np.array([[0, 1], [1, 2]])
    tiled = _tile_bonds(bonds, 4, 2)
    assert np.array_equal(tiled, np.array([[0, 1], [1, 2], [4, 5], [5, 6]]))


def test_cell_edge_face_mode_drops_depth_edges() -> None:
    pv = pytest.importorskip("pyvista")
    origin = np.zeros(3)
    vectors = np.eye(3)
    box = _cell_edge_polydata(pv, origin, vectors, mode="box")
    face = _cell_edge_polydata(
        pv, origin, vectors, look=np.array([1.0, 0.0, 0.0]), mode="face"
    )
    assert box.n_lines == 12
    assert face.n_lines == 4


def _unique_points(polydata) -> np.ndarray:
    return np.unique(np.round(np.asarray(polydata.points), decimals=8), axis=0)


def test_face_mode_selects_opposite_faces_for_opposite_looks() -> None:
    pv = pytest.importorskip("pyvista")
    origin = np.array([1.0, -2.0, 0.5])
    cells = (
        np.eye(3),
        np.array([[2.0, 0.0, 0.0], [0.6, 1.5, 0.0], [0.2, -0.3, 1.0]]),
        np.diag([-2.0, 3.0, 1.0]),
    )
    for vectors in cells:
        for axis in range(3):
            look = np.zeros(3)
            look[axis] = 1.0
            near = _cell_edge_polydata(pv, origin, vectors, look=look, mode="face")
            far = _cell_edge_polydata(pv, origin, vectors, look=-look, mode="face")
            near_pts = _unique_points(near)
            far_pts = _unique_points(far)
            assert len(near_pts) == 4
            assert len(far_pts) == 4
            shared = np.any(
                np.all(np.isclose(near_pts[:, None, :], far_pts[None, :, :]), axis=2)
            )
            assert not shared
            assert float(np.dot(near_pts.mean(axis=0), look)) < float(
                np.dot(far_pts.mean(axis=0), look)
            )


def test_skew_nonaxial_look_uses_reciprocal_column_normals() -> None:
    """A skew cell and a diagonal look must use reciprocal columns.

    Opposite-look checks can pass while the selected face still points
    away from the camera. Lattice rows make ``inv[:, axis]`` the outward
    normal of ``f=1``. ``inv[axis]`` is a different direction.
    """

    from mat_viewer.render._vtk_cube_geometry import _CELL_FACES, _camera_face

    vectors = np.array([[1.0, 0.0, 0.0], [2.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    look = np.array([-1.0, -1.0, 0.0])
    direction = look / np.linalg.norm(look)
    selected = _camera_face(vectors, direction)
    # f=1 on axis 1. The f=1 face on axis 0 has true outward (1, -2, 0),
    # and (1, -2, 0) · (1, 1, 0) = -1, so that face points away.
    assert selected == (2, 4, 7, 6)
    assert selected != (1, 4, 7, 5)
    assert float(np.dot(np.array([1.0, -2.0, 0.0]), np.array([1.0, 1.0, 0.0]))) < 0.0

    inverse = np.linalg.inv(vectors)
    looks = (
        look,
        np.array([1.0, -0.5, 0.25]),
        np.array([-0.2, 1.0, -0.7]),
        np.array([0.3, 0.3, 1.0]),
    )
    for raw_look in looks:
        direction = raw_look / np.linalg.norm(raw_look)
        best_cycle = None
        best_score = -np.inf
        for cycle, axis, side in _CELL_FACES:
            outward = float(side) * inverse[:, axis]
            outward = outward / np.linalg.norm(outward)
            score = float(np.dot(outward, -direction))
            if score > best_score:
                best_score = score
                best_cycle = cycle
        assert _camera_face(vectors, direction) == best_cycle
        assert best_score > 0.0


def test_bond_segments_split_by_endpoint_element_color() -> None:
    xyz = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    numbers = np.array([6, 7])
    colors = {6: (0.6, 0.6, 0.6), 7: (0.3, 0.5, 1.0)}
    radii = {6: 0.4, 7: 0.3}
    groups = _bond_segments(xyz, numbers, [(0, 1)], colors, None, radii)
    assert set(groups) == {(0.6, 0.6, 0.6), (0.3, 0.5, 1.0)}
    for bucket in groups.values():
        assert len(bucket["lines"]) == 1
    gray = np.asarray(groups[(0.6, 0.6, 0.6)]["points"])
    assert gray[0, 0] == pytest.approx(0.4)
    assert gray[1, 0] == pytest.approx(1.0)
    uniform = _bond_segments(xyz, numbers, [(0, 1)], colors, (0.2, 0.2, 0.2))
    assert set(uniform) == {(0.2, 0.2, 0.2)}
    assert len(uniform[(0.2, 0.2, 0.2)]["lines"]) == 1


def test_asymmetric_green_stop_matches_the_data_zero() -> None:
    stops = [(0, 40, 230), (0, 190, 210), (30, 210, 40), (240, 210, 0), (210, 20, 20)]
    colors = _colormap(stops, -0.04, 0.02, color_center=0.0)
    zero = (0.0 - (-0.04)) / (0.02 - (-0.04))
    index = round(zero * 255)
    assert np.allclose(colors[index], np.array([30, 210, 40]) / 255.0, atol=0.02)


def test_linear_colormap_does_not_pin_zero_when_center_is_omitted() -> None:
    stops = [(0, 0, 255), (0, 128, 255), (0, 255, 0), (255, 255, 0), (255, 0, 0)]
    colors = _colormap(stops, 0.01, 0.05)
    assert np.allclose(colors[0], np.array([0, 0, 255]) / 255.0)
    assert np.allclose(colors[-1], np.array([255, 0, 0]) / 255.0)
    zero = (0.0 - 0.01) / (0.05 - 0.01)
    assert not np.allclose(
        colors[round(zero * 255)],
        np.array([0, 255, 0]) / 255.0,
        atol=0.02,
    )


def test_equal_color_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="increasing"):
        _colormap(None, 0.2, 0.2)


def test_color_center_outside_the_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="color_center"):
        _colormap(None, 0.1, 0.4, color_center=0.0)


def test_two_stops_are_accepted() -> None:
    colors = _colormap([(0, 0, 0), (255, 255, 255)], 0.0, 1.0)
    assert np.allclose(colors[0], 0.0)
    assert np.allclose(colors[-1], 1.0)


def test_color_limits_follow_the_grid() -> None:
    grid = np.full((2, 2, 2), 0.2)
    grid[1, 1, 1] = 1.1
    assert _color_limits(grid, None, None) == (0.2, 1.1)


def test_negative_bond_index_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _repeated_atoms(
            np.zeros((2, 3)),
            np.array([1, 1]),
            np.array([[0, -1]]),
            np.eye(3),
            (1, 1, 1),
            None,
        )


def test_repeat_count_must_be_positive() -> None:
    iso, color, origin, axes = _tiny_fields()
    with pytest.raises(ValueError, match="repeat"):
        render_cube_figure(
            iso,
            color,
            origin,
            axes,
            np.array([1]),
            np.zeros((1, 3)),
            Path("unused.png"),
            look=np.array([0.0, 0.0, 1.0]),
            up=np.array([0.0, 1.0, 0.0]),
            parallel_scale=1.0,
            width=16,
            height=16,
            element_color={1: (0.2, 0.2, 0.2)},
            element_radius={1: 0.1},
            repeat=(0, 1, 1),
        )


def test_bond_finder_sees_the_repeated_coordinates() -> None:
    lattice = np.array([[2.0, 0.4, 0.0], [0.0, 3.0, 0.0], [0.1, 0.0, 4.0]])
    xyz = np.array([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    seen = {}

    def finder(coords, numbers):
        seen["coords"] = np.array(coords)
        seen["numbers"] = np.array(numbers)
        return [[0, 3]]

    draw_xyz, draw_numbers, draw_bonds, shifts = _repeated_atoms(
        xyz,
        np.array([3, 5]),
        np.zeros((0, 2), dtype=int),
        lattice,
        (2, 1, 1),
        finder,
    )
    assert len(shifts) == 2
    assert np.allclose(shifts[1], lattice[0])
    assert np.allclose(draw_xyz[2], xyz[0] + lattice[0])
    assert np.array_equal(draw_numbers, [3, 5, 3, 5])
    assert np.allclose(seen["coords"], draw_xyz)
    assert np.array_equal(draw_bonds, [[0, 3]])


def test_sheared_sample_uses_voxel_rows() -> None:
    shape = (4, 5, 6)
    ii, jj, _kk = np.mgrid[0:shape[0], 0:shape[1], 0:shape[2]]
    color = 10.0 * ii + jj
    origin = np.array([0.2, -0.4, 0.5])
    axes = np.array(
        [[0.30, 0.00, 0.00], [0.10, 0.40, 0.00], [0.00, 0.05, 0.25]]
    )
    index = np.array([[1.5, 2.0, 0.25]])
    world = origin + index @ axes
    sampled = _sample_world(color, world, origin, axes, periodic=False)
    expected = 0.5 * (10.0 * 1.0 + 2.0) + 0.5 * (10.0 * 2.0 + 2.0)
    assert np.allclose(sampled, expected)
    spacing = np.linalg.norm(axes, axis=1)
    orthogonal = origin + index * spacing
    assert not np.allclose(world, orthogonal, atol=1e-6)


def test_nonperiodic_sample_clamps_instead_of_wrapping() -> None:
    color = np.zeros((4, 2, 2))
    color[:, 0, 0] = np.array([0.0, 1.0, 2.0, 3.0])
    outside = _sample_indices(color, np.array([[-0.5, 0.0, 0.0]]), periodic=False)
    wrapped = _sample_indices(color, np.array([[-0.5, 0.0, 0.0]]), periodic=True)
    assert np.allclose(outside, 0.0)
    assert np.allclose(wrapped, 1.5)
    assert np.allclose(
        _sample_indices(color, np.array([[3.5, 0.0, 0.0]]), periodic=True),
        1.5,
    )


def test_periodic_closure_copies_the_first_plane() -> None:
    values = np.arange(24, dtype=float).reshape(2, 3, 4)
    closed = _closed_values(values, True)
    assert closed.shape == (3, 4, 5)
    assert np.allclose(closed[-1], closed[0])
    assert np.allclose(closed[:, -1], closed[:, 0])
    assert np.allclose(closed[:, :, -1], closed[:, :, 0])


def test_periodic_isosurface_reaches_sheared_cell_faces() -> None:
    pytest.importorskip("skimage")
    shape = (9, 10, 12)
    u = np.arange(shape[0]) / shape[0]
    v = np.arange(shape[1]) / shape[1]
    w = np.arange(shape[2]) / shape[2]
    values = (
        np.cos(2 * np.pi * u[:, None, None])
        + np.cos(2 * np.pi * v[None, :, None])
        + np.cos(2 * np.pi * w[None, None, :])
    )
    from skimage.measure import marching_cubes

    indices, _faces = _marching_indices(values, 2.2, True, marching_cubes)
    fractional = indices / np.asarray(shape, dtype=float)
    assert np.all(fractional >= -1e-8)
    assert np.all(fractional <= 1.0 + 1e-8)
    for axis in range(3):
        assert np.any(np.isclose(fractional[:, axis], 0.0, atol=1e-6))
        assert np.any(np.isclose(fractional[:, axis], 1.0, atol=1e-6))
    axes = np.array([[0.30, 0.00, 0.00], [0.08, 0.40, 0.00], [0.00, 0.02, 0.25]])
    origin = np.array([0.2, -0.3, 0.4])
    world = origin + indices @ axes
    spacing = np.linalg.norm(axes, axis=1)
    assert not np.allclose(world, origin + indices * spacing, atol=1e-6)
    recovered = _sample_world(values, world, origin, axes, periodic=True)
    assert np.allclose(recovered, 2.2, atol=1e-5)


def test_scene_extent_includes_the_mesh_when_there_are_no_atoms() -> None:
    center, radius = _scene_extent(
        np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]),
        np.zeros((0, 3)),
    )
    assert np.allclose(center, [5.0, 0.0, 0.0])
    assert radius == pytest.approx(5.0)


def test_lights_stay_outside_a_large_cell() -> None:
    corners = np.array(
        [
            [sign_x * 100.0, sign_y * 100.0, sign_z * 100.0]
            for sign_x in (-1.0, 1.0)
            for sign_y in (-1.0, 1.0)
            for sign_z in (-1.0, 1.0)
        ]
    )
    center, radius = _scene_extent(corners, np.zeros((0, 3)))
    camera, focal, _up, lights, clipping = _view_rig(
        center,
        radius,
        np.array([0.0, 0.0, 1.0]),
        np.array([0.0, 1.0, 0.0]),
    )
    assert np.allclose(focal, center)
    distance = float(np.linalg.norm(camera - center))
    assert distance > 100.0
    assert distance == pytest.approx(4.0 * radius)
    for position, _intensity in lights:
        assert float(np.linalg.norm(position - center)) > radius
    assert clipping[0] > 0.0
    assert clipping[0] < distance - radius
    assert clipping[1] > distance + radius


def test_parallel_look_and_up_are_rejected() -> None:
    with pytest.raises(ValueError, match="parallel"):
        _view_rig(
            np.zeros(3),
            1.0,
            np.array([0.0, 0.0, 2.0]),
            np.array([0.0, 0.0, -1.0]),
        )


def test_depth_peeling_does_not_open_a_probe_window() -> None:
    class _Window:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def SetAlphaBitPlanes(self, value) -> None:
            self.calls.append(("alpha", value))

        def SetMultiSamples(self, value) -> None:
            self.calls.append(("samples", value))

        def SetUseDepthPeeling(self, value) -> None:
            self.calls.append(("peel", bool(value)))

        def SetMaximumNumberOfPeels(self, value) -> None:
            self.calls.append(("max", int(value)))

        def SetOcclusionRatio(self, value) -> None:
            self.calls.append(("occlusion", float(value)))

    idle = _Window()
    plotter = type("Plotter", (), {"render_window": idle, "renderer": idle})()
    _apply_depth_peeling(plotter, 1)
    assert idle.calls == []
    _apply_depth_peeling(plotter, 4)
    assert ("peel", True) in idle.calls
    assert ("max", 4) in idle.calls


_OPENGL_WINDOW = "VTK_DEFAULT_OPENGL_WINDOW"


def _clear_opengl_window(monkeypatch) -> None:
    """Drop the window class and remember to undo a later direct assignment.

    ``MonkeyPatch.delenv(..., raising=False)`` records no undo when the
    variable is already absent. ``_prepare_vtk_window`` then writes
    ``os.environ`` itself. Recording an empty value first makes that
    undo delete the name again.
    """

    if _OPENGL_WINDOW not in os.environ:
        monkeypatch.setenv(_OPENGL_WINDOW, "")
    monkeypatch.delenv(_OPENGL_WINDOW, raising=False)


# Captured before any test in this module runs. The render that follows the
# window-class fixture compares against this, after every finalizer.
_OPENGL_WINDOW_AT_IMPORT = os.environ.get(_OPENGL_WINDOW)


def _assign_opengl_window(value: str | None) -> None:
    if value is None:
        os.environ.pop(_OPENGL_WINDOW, None)
    else:
        os.environ[_OPENGL_WINDOW] = value


@pytest.fixture(autouse=True)
def _restore_opengl_window_after_test(monkeypatch) -> None:
    """Restore the caller's window class after the shared monkeypatch undo.

    ``tests/conftest.py`` requests ``monkeypatch`` from an autouse fixture,
    so that fixture is created first and its ``undo`` runs last. A restore
    that only runs inside this fixture is overwritten when ``undo`` replays
    a later ``delenv`` of a temporary class. Registering the original value
    first makes that last undo step put the original back, whether it was
    absent or set.
    """

    previous = os.environ.get(_OPENGL_WINDOW)
    if previous is None:
        monkeypatch.setenv(_OPENGL_WINDOW, "")
        monkeypatch.delenv(_OPENGL_WINDOW, raising=False)
    else:
        monkeypatch.setenv(_OPENGL_WINDOW, previous)
    yield
    # ``undo`` may still be pending. Write the original now, and again if
    # this finalizer is the last one to run.
    _assign_opengl_window(previous)


def _linux_without_display(monkeypatch) -> None:
    monkeypatch.setattr(vtk_cube.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _clear_opengl_window(monkeypatch)


def test_missing_headless_backend_raises(monkeypatch) -> None:
    _linux_without_display(monkeypatch)
    monkeypatch.setattr(vtk_cube, "_shared_library", lambda _name: None)
    with pytest.raises(RuntimeError, match="OSMesa"):
        _prepare_vtk_window()


def test_headless_selection_prefers_osmesa(monkeypatch) -> None:
    _linux_without_display(monkeypatch)

    def libraries(name: str) -> str | None:
        return "libOSMesa.so.8" if name == "OSMesa" else None

    monkeypatch.setattr(vtk_cube, "_shared_library", libraries)
    _prepare_vtk_window()
    assert os.environ["VTK_DEFAULT_OPENGL_WINDOW"] == "vtkOSOpenGLRenderWindow"


def test_display_does_not_override_the_window_class(monkeypatch) -> None:
    monkeypatch.setattr(vtk_cube.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":1")
    _clear_opengl_window(monkeypatch)
    _prepare_vtk_window()
    assert _OPENGL_WINDOW not in os.environ


@pytest.mark.parametrize(
    ("platform", "window"),
    [
        ("win32", "vtkWin32OpenGLRenderWindow"),
        ("darwin", "vtkCocoaOpenGLRenderWindow"),
    ],
)
def test_desktop_platforms_select_a_native_window(monkeypatch, platform, window) -> None:
    monkeypatch.setattr(vtk_cube.sys, "platform", platform)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    _clear_opengl_window(monkeypatch)
    monkeypatch.setattr(vtk_cube, "_shared_library", lambda _name: None)
    _prepare_vtk_window()
    assert os.environ["VTK_DEFAULT_OPENGL_WINDOW"] == window


def test_explicit_window_class_overrides_the_native_default(monkeypatch) -> None:
    monkeypatch.setattr(vtk_cube.sys, "platform", "win32")
    monkeypatch.setenv("VTK_DEFAULT_OPENGL_WINDOW", "vtkOSOpenGLRenderWindow")
    monkeypatch.setattr(vtk_cube, "_shared_library", lambda _name: None)
    _prepare_vtk_window()
    assert os.environ["VTK_DEFAULT_OPENGL_WINDOW"] == "vtkOSOpenGLRenderWindow"


def test_one_taubin_pass_does_not_collapse_the_surface() -> None:
    pytest.importorskip("pyvista")
    pytest.importorskip("skimage")
    from skimage.measure import marching_cubes

    iso, _color, origin, axes, _center = _blob_fields()
    vertices, faces = _marching_indices(iso, 0.45, False, marching_cubes)
    world = origin + vertices @ axes
    import pyvista as pv

    mesh = pv.PolyData(world, np.column_stack([np.full(len(faces), 3), faces]).ravel())
    smoothed = _smooth_taubin(mesh, 1, 0.1)
    before = np.asarray(mesh.points)
    after = np.asarray(smoothed.points)
    before_span = float(np.linalg.norm(before.max(axis=0) - before.min(axis=0)))
    after_span = float(np.linalg.norm(after.max(axis=0) - after.min(axis=0)))
    assert after_span > 0.8 * before_span
    shift = float(np.linalg.norm(after.mean(axis=0) - before.mean(axis=0)))
    assert shift < 0.05 * before_span


def test_repeat_translates_mesh_points_by_the_cell_vector() -> None:
    pv = pytest.importorskip("pyvista")
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    mesh = pv.PolyData(points, np.array([3, 0, 1, 2]))
    mesh.point_data[_SCALAR] = np.array([0.2, 0.4, 0.6])
    lattice = np.array([[2.0, 0.5, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 4.0]])
    shifts = _cell_shifts(lattice, (2, 1, 1))
    merged = _repeat_mesh(mesh, shifts)
    found = np.asarray(merged.points)
    for point in np.vstack([points, points + lattice[0]]):
        assert np.any(np.linalg.norm(found - point, axis=1) < 1e-8)
    scalars = np.sort(np.asarray(merged.point_data[_SCALAR]))
    assert np.allclose(scalars, [0.2, 0.2, 0.4, 0.4, 0.6, 0.6])


def test_opengl_window_fixture_restores_before_rendering() -> None:
    """A local patch restores an absent variable and any caller-supplied class.

    The shared ``monkeypatch`` fixture is not used. Its later ``undo`` must
    still leave the process value that was present when this test started;
    the following render checks that after every finalizer.
    """

    def prefer_osmesa(name: str) -> str | None:
        return "libOSMesa.so.8" if name == "OSMesa" else None

    def select_osmesa() -> None:
        with pytest.MonkeyPatch.context() as local:
            local.setattr(vtk_cube.sys, "platform", "linux")
            local.delenv("DISPLAY", raising=False)
            local.delenv("WAYLAND_DISPLAY", raising=False)
            _clear_opengl_window(local)
            local.setattr(vtk_cube, "_shared_library", prefer_osmesa)
            _prepare_vtk_window()
            assert os.environ[_OPENGL_WINDOW] == "vtkOSOpenGLRenderWindow"

    os.environ.pop(_OPENGL_WINDOW, None)
    select_osmesa()
    assert _OPENGL_WINDOW not in os.environ

    os.environ[_OPENGL_WINDOW] = "vtkWin32OpenGLRenderWindow"
    select_osmesa()
    assert os.environ[_OPENGL_WINDOW] == "vtkWin32OpenGLRenderWindow"

    os.environ[_OPENGL_WINDOW] = "vtkEGLRenderWindow"
    select_osmesa()
    assert os.environ[_OPENGL_WINDOW] == "vtkEGLRenderWindow"


def test_render_follows_window_fixture_teardown(tmp_path: Path) -> None:
    """Render immediately after the window-class fixture's finalizers.

    This stays directly after
    ``test_opengl_window_fixture_restores_before_rendering``. That test
    leaves ``vtkEGLRenderWindow`` set until teardown. The shared
    monkeypatch ``undo`` runs after this module's fixture, so the class
    must be gone before this render. An inherited EGL class aborts native
    VTK when the machine has libGL and neither EGL nor OSMesa.
    """

    assert os.environ.get(_OPENGL_WINDOW) == _OPENGL_WINDOW_AT_IMPORT
    if _OPENGL_WINDOW_AT_IMPORT is not None:
        # The caller already selected a window class. Rendering that class
        # is outside this regression; the absent case is the one that must
        # reach VTK without an inherited class.
        return
    pytest.importorskip("pyvista")
    pytest.importorskip("skimage")
    output = tmp_path / "after-teardown.png"
    render_cube_figure(*_blob(), output, **_figure_kwargs())
    _assert_figure(output)


def test_repeated_render_is_identical(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    pytest.importorskip("skimage")
    import pyvista as pv

    off_screen = pv.OFF_SCREEN
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    kwargs = _figure_kwargs()
    render_cube_figure(*_blob(), first, **kwargs)
    render_cube_figure(*_blob(), second, **kwargs)
    assert pv.OFF_SCREEN == off_screen
    assert first.read_bytes() == second.read_bytes()
    _assert_figure(first)


def test_volume_without_atoms_renders(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    pytest.importorskip("skimage")
    iso, color, origin, axes, _center = _blob_fields()
    output = tmp_path / "volume.png"
    kwargs = _figure_kwargs()
    kwargs["element_color"] = {}
    kwargs["element_radius"] = {}
    kwargs["bonds"] = np.zeros((0, 2), dtype=int)
    kwargs["taubin_iterations"] = 1
    render_cube_figure(
        iso,
        color,
        origin,
        axes,
        np.zeros(0, dtype=int),
        np.zeros((0, 3)),
        output,
        **kwargs,
    )
    _assert_figure(output)


def test_headless_process_does_not_abort(tmp_path: Path) -> None:
    pytest.importorskip("pyvista")
    pytest.importorskip("skimage")
    output = tmp_path / "headless.png"
    script = """
import os
import sys
from pathlib import Path

import numpy as np

os.environ.pop("DISPLAY", None)
os.environ.pop("WAYLAND_DISPLAY", None)
os.environ.pop("VTK_DEFAULT_OPENGL_WINDOW", None)

from mat_viewer.render.vtk_cube import render_cube_figure

shape = (12, 12, 12)
index = np.indices(shape, dtype=float)
center = (np.array(shape) - 1.0) / 2.0
radius2 = np.sum((index - center[:, None, None, None]) ** 2, axis=0)
rho = np.exp(-radius2 / 6.0)
origin = np.zeros(3)
axes = np.eye(3) * 0.25
atom = origin + center @ axes
render_cube_figure(
    rho,
    rho - 0.2,
    origin,
    axes,
    np.array([1]),
    atom.reshape(1, 3),
    Path(sys.argv[1]),
    look=np.array([0.0, 0.0, 1.0]),
    up=np.array([0.0, 1.0, 0.0]),
    parallel_scale=1.2,
    width=80,
    height=64,
    element_color={1: (0.1, 0.2, 0.8)},
    element_radius={1: 0.2},
    isovalue=0.35,
    opacity=1.0,
    taubin_iterations=1,
    depth_peels=4,
    sphere_resolution=8,
)
print("ok")
"""
    env = os.environ.copy()
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("VTK_DEFAULT_OPENGL_WINDOW", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    proc = subprocess.run(
        [sys.executable, "-c", script, str(output)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode >= 0, proc.stderr[-2000:]
    if proc.returncode != 0:
        combined = proc.stderr + proc.stdout
        assert "OSMesa" in combined or "EGL" in combined
        return
    assert "ok" in proc.stdout
    _assert_figure(output)


def test_figure_shade_and_kwargs_resolve_the_same_numbers() -> None:
    coefficients = (0.25, 0.5, 0.1, 12.0)
    ambient, diffuse, specular, shininess = coefficients
    record = FigureShade(
        ambient=ambient,
        diffuse=diffuse,
        specular=specular,
        shininess=shininess,
    )
    from_record = _resolve_surface_shade(record)
    from_kwargs = _resolve_surface_shade(
        None,
        surface_ambient=ambient,
        surface_diffuse=diffuse,
        surface_specular=specular,
        surface_specular_power=shininess,
    )
    from_mapping = _resolve_surface_shade(
        {
            "ambient": ambient,
            "diffuse": diffuse,
            "specular": specular,
            "shininess": shininess,
        }
    )
    assert from_record == from_kwargs == from_mapping == coefficients


def test_omitted_surface_keeps_the_historical_shade() -> None:
    assert _resolve_surface_shade(None) == (
        vtk_cube._SURFACE_AMBIENT,
        vtk_cube._SURFACE_DIFFUSE,
        vtk_cube._SURFACE_SPECULAR,
        vtk_cube._SURFACE_SPECULAR_POWER,
    )
    parameters = inspect.signature(render_cube_figure).parameters
    assert repr(parameters["surface_ambient"].default) == repr(vtk_cube._SURFACE_AMBIENT)
    assert repr(parameters["surface_diffuse"].default) == repr(vtk_cube._SURFACE_DIFFUSE)
    assert repr(parameters["surface_specular"].default) == repr(vtk_cube._SURFACE_SPECULAR)
    assert repr(parameters["surface_specular_power"].default) == repr(
        vtk_cube._SURFACE_SPECULAR_POWER
    )


@pytest.mark.parametrize(
    "override",
    [
        {"surface_ambient": 0.25},
        {"surface_diffuse": 0.5},
        {"surface_specular": 0.1},
        {"surface_specular_power": 12},
    ],
)
def test_surface_record_and_kwargs_cannot_combine(override: dict[str, float]) -> None:
    record = FigureShade(ambient=0.25, diffuse=0.5, specular=0.1, shininess=12)
    with pytest.raises(ValueError, match="not both"):
        _resolve_surface_shade(record, **override)


def test_figure_shade_uses_shared_coefficient_bounds() -> None:
    with pytest.raises(ValueError, match="ambient"):
        FigureShade(ambient=-0.01, diffuse=0.2, specular=0.0, shininess=2)
    with pytest.raises(ValueError, match="diffuse"):
        FigureShade(ambient=0.2, diffuse=float("nan"), specular=0.0, shininess=2)
    with pytest.raises(ValueError, match="specular"):
        FigureShade(ambient=0.2, diffuse=0.2, specular=float("inf"), shininess=2)
    with pytest.raises(ValueError, match="shininess"):
        FigureShade(ambient=0.2, diffuse=0.2, specular=0.0, shininess=0.5)
    allowed = FigureShade(ambient=1.4, diffuse=0.0, specular=0.0, shininess=1)
    assert allowed.shininess == 1.0
    assert _resolve_surface_shade(allowed) == (1.4, 0.0, 0.0, 1.0)
    with pytest.raises(ValueError, match="surface_ambient"):
        _resolve_surface_shade(None, surface_ambient=1.4)


def test_surface_mapping_requires_the_four_shade_fields() -> None:
    with pytest.raises(ValueError, match="shininess"):
        _resolve_surface_shade({"ambient": 0.2, "diffuse": 0.3, "specular": 0.1})
    with pytest.raises(ValueError, match="shininess"):
        _resolve_surface_shade(
            {
                "ambient": 0.2,
                "diffuse": 0.3,
                "specular": 0.1,
                "shininess": 8,
                "opacity": 1.0,
            }
        )


def test_render_cube_figure_rejects_both_shade_forms(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        render_cube_figure(
            *_blob(),
            tmp_path / "unused.png",
            surface=FigureShade(ambient=0.25, diffuse=0.5, specular=0.1, shininess=12),
            surface_ambient=0.25,
            **_figure_kwargs(),
        )


def _tiny_fields():
    grid = np.zeros((2, 2, 2))
    grid[0, 0, 0] = 1.0
    return grid, grid, np.zeros(3), np.eye(3)


def _blob_fields():
    shape = (16, 16, 16)
    index = np.indices(shape, dtype=float)
    center = (np.array(shape) - 1.0) / 2.0
    radius2 = np.sum((index - center[:, None, None, None]) ** 2, axis=0)
    rho = np.exp(-radius2 / 8.0)
    color = (index[0] - center[0]) / shape[0]
    origin = np.array([0.4, -0.2, 0.1])
    axes = np.diag([0.2, 0.2, 0.2])
    return rho, color, origin, axes, center


def _blob():
    rho, color, origin, axes, center = _blob_fields()
    atom = origin + center @ axes
    xyz = np.vstack([atom, atom + np.array([0.35, 0.0, 0.0])])
    return rho, color, origin, axes, np.array([1, 1]), xyz


def _figure_kwargs():
    return {
        "isovalue": 0.45,
        "color_center": 0.0,
        "opacity": 0.8,
        "taubin_iterations": 2,
        "look": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "parallel_scale": 2.2,
        "width": 80,
        "height": 64,
        "element_color": {1: (0.15, 0.2, 0.75)},
        "element_radius": {1: 0.12},
        "bonds": np.array([[0, 1]]),
        "bond_radius": 0.04,
        "sphere_resolution": 8,
        "depth_peels": 4,
    }


def _assert_figure(path: Path) -> None:
    image = Image.open(path)
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.getchannel("A"))
        rgb = np.asarray(background)
    else:
        rgb = np.asarray(image.convert("RGB"))
    assert rgb.ndim == 3 and rgb.shape[2] == 3
    assert float(rgb.std()) > 5.0
    near_white = np.all(rgb >= 250, axis=2)
    assert int(np.count_nonzero(~near_white)) > 20
    assert int(np.count_nonzero(near_white)) > 20
    rows, cols = np.nonzero(~near_white)
    assert int(cols.max() - cols.min()) >= 8
    assert int(rows.max() - rows.min()) >= 8
