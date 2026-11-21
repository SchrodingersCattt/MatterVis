"""Cube isosurface auto-view: mesh bounds, side-on lobes, and general-renderer zoom."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import numpy as np
import pytest

from mat_viewer.cli import _camera_spec
from mat_viewer.cube.cpu import cube_isosurface_meshes
from mat_viewer.cube.io import CubeAtom, CubeData
from mat_viewer.render.contracts import CameraSpec, LinePrimitive, TriangleMeshPrimitive
from mat_viewer.render.geometry import unit_cell_primitive
from mat_viewer.render.isosurface_framing import (
    cube_grid_corner_extents,
    isosurface_mesh_extents,
    isosurface_mesh_vertices,
    orthographic_scale_for_points,
    resolve_content_view,
    view_direction_for_content,
)
from mat_viewer.render.planning import prepare_render
from mat_viewer.render.viewport import _scene_ranges


def _camera_args(**overrides) -> Namespace:
    values = {
        "output": "figure.png",
        "width": 900,
        "height": 720,
        "show_hydrogen": True,
        "show_unit_cell": True,
        "camera_axis": None,
        "view_direction": None,
        "camera_position": None,
        "camera_up": None,
        "camera_target": None,
        "camera_distance": 1.8,
        "projection": "orthographic",
        "ortho_scale": None,
        "style": "ball_stick",
        "atom_scale": 1.0,
        "bond_radius": 0.15,
        "min_atom_radius": None,
        "isovalue": 0.45,
        "isosurface_stride": 1,
        "zoom": 1.0,
        "framing_margin": 1.12,
        "view": "auto",
    }
    values.update(overrides)
    return Namespace(**values)


def _field_cube(
    values: np.ndarray,
    *,
    spacing: float,
    atom_index: tuple[float, float, float],
) -> CubeData:
    axes = np.eye(3) * float(spacing)
    origin = np.zeros(3)
    cart = axes.T @ np.asarray(atom_index, dtype=float)
    return CubeData(
        title="synthetic",
        comment="",
        atoms=[CubeAtom(atomic_number=8, charge=0.0, coord=cart.copy())],
        origin=origin,
        axes=axes,
        values=np.asarray(values, dtype=float),
        path=SimpleNamespace(),  # type: ignore[arg-type]
    )


def _structure(cube: CubeData, *, atom_cart: np.ndarray) -> SimpleNamespace:
    scene = {
        "M": cube.lattice,
        "cube_data": cube,
        "draw_atoms": [
            {
                "elem": "O",
                "cart": np.asarray(atom_cart, dtype=float),
                "atom_radius": 0.6,
                "label": "O1",
            }
        ],
        "bounds": {
            "center": (cube.lattice.sum(axis=0) * 0.5).tolist(),
            "mins": [0.0, 0.0, 0.0],
            "maxs": cube.lattice.sum(axis=0).tolist(),
        },
    }
    bundle = SimpleNamespace(M=cube.lattice, cube_data=cube, scene=scene)
    return SimpleNamespace(
        frames=(SimpleNamespace(bundle=bundle),),
        input_format="cube",
    )


def _padded_orbital():
    shape = (22, 22, 22)
    coords = np.indices(shape, dtype=float)
    centre = np.array([4.0, 5.0, 4.5])
    spacing = 0.55
    radius = np.sqrt(sum((coords[axis] - centre[axis]) ** 2 for axis in range(3)))
    values = np.exp(-0.5 * (radius / 1.15) ** 2)
    cube = _field_cube(values, spacing=spacing, atom_index=tuple(centre))
    atom = (centre * spacing)
    return _structure(cube, atom_cart=atom), cube, atom


def _pz_orbital():
    shape = (18, 18, 18)
    coords = np.indices(shape, dtype=float)
    centre = np.array([9.0, 9.0, 9.0])
    spacing = 0.4
    xy = (coords[0] - centre[0]) ** 2 + (coords[1] - centre[1]) ** 2
    z = coords[2] - centre[2]
    positive = np.exp(-0.5 * ((xy + (z - 3.2) ** 2) / 1.35**2))
    negative = np.exp(-0.5 * ((xy + (z + 3.2) ** 2) / 1.35**2))
    values = positive - negative
    cube = _field_cube(values, spacing=spacing, atom_index=tuple(centre))
    atom = centre * spacing
    return _structure(cube, atom_cart=atom), cube


def _direction(camera: CameraSpec) -> np.ndarray:
    vector = np.asarray(camera.position) - np.asarray(camera.target)
    return vector / np.linalg.norm(vector)


def test_mesh_extents_follow_vertices_not_grid_corners() -> None:
    structure, _cube, _atom = _padded_orbital()
    scene = structure.frames[0].bundle.scene
    mesh_min, mesh_max = isosurface_mesh_extents(
        scene, {"isosurface_isovalue": 0.45, "isosurface_stride": 1}
    )
    grid_min, grid_max = cube_grid_corner_extents(scene)

    assert mesh_min is not None and mesh_max is not None
    mesh_span = float(np.linalg.norm(mesh_max - mesh_min))
    grid_span = float(np.linalg.norm(grid_max - grid_min))
    assert mesh_span < grid_span * 0.45
    assert not np.allclose(mesh_min, grid_min)
    assert not np.allclose(mesh_max, grid_max)


def test_padded_grid_auto_view_frames_the_orbital_not_the_cell() -> None:
    structure, cube, atom = _padded_orbital()
    args = _camera_args(show_unit_cell=True)
    camera = _camera_spec(structure, args, display="unit_cell")

    cell_centre = 0.5 * cube.lattice.sum(axis=0)
    assert camera.target == pytest.approx(args.camera_fit_record["target"])
    assert np.linalg.norm(np.asarray(camera.target) - atom) < 1.6
    assert np.linalg.norm(np.asarray(camera.target) - cell_centre) > 4.0
    assert camera.ortho_scale < 3.5
    assert args.camera_fit_record["mode"] == "isosurface_content"
    assert args.camera_fit_record["includes_cell"] is False
    aabb = np.asarray(args.camera_fit_record["aabb_max"]) - np.asarray(
        args.camera_fit_record["aabb_min"]
    )
    assert float(np.max(aabb)) < 6.0


def test_plotly_scene_ranges_ignore_grid_and_cell_for_an_isosurface() -> None:
    structure, cube, atom = _padded_orbital()
    scene = structure.frames[0].bundle.scene
    style = {
        "display_mode": "unit_cell",
        "show_unit_cell": True,
        "isosurface_enabled": True,
        "isosurface_isovalue": 0.45,
        "isosurface_stride": 1,
        "style": "ball_stick",
    }
    ranges = _scene_ranges(scene, style)
    span = np.array([axis[1] - axis[0] for axis in ranges], dtype=float)
    centre = np.array([0.5 * (axis[0] + axis[1]) for axis in ranges], dtype=float)
    cell_span = np.linalg.norm(cube.lattice, axis=1)

    assert float(np.max(span)) < float(np.min(cell_span)) * 0.55
    assert np.linalg.norm(centre - atom) < 2.0


def test_pz_auto_view_is_side_on_and_explicit_c_star_stays_end_on() -> None:
    structure, _cube = _pz_orbital()
    auto = _camera_spec(structure, _camera_args(), display="unit_cell")
    end_on = _camera_spec(
        structure,
        _camera_args(camera_axis="c*"),
        display="unit_cell",
    )

    assert abs(float(np.dot(_direction(auto), [0.0, 0.0, 1.0]))) < 0.45
    assert abs(float(np.dot(_direction(end_on), [0.0, 0.0, 1.0]))) > 0.95


def test_flat_contact_is_not_viewed_along_its_long_axis() -> None:
    # Geometric stand-in for a hydrogen-bonded pair lying in the xy plane:
    # long along the contact, thin along z. No element or bond heuristic.
    x = np.linspace(-2.4, 2.4, 18)
    y = np.linspace(-0.8, 0.8, 8)
    grid_x, grid_y = np.meshgrid(x, y)
    points = np.column_stack(
        (grid_x.ravel(), grid_y.ravel(), np.zeros(grid_x.size))
    )
    direction, _up = view_direction_for_content(points)
    long_axis = np.array([1.0, 0.0, 0.0])
    plane_normal = np.array([0.0, 0.0, 1.0])

    assert abs(float(np.dot(direction, long_axis))) < 0.45
    assert abs(float(np.dot(direction, plane_normal))) > 0.7


def test_isotropic_surface_uses_a_diagonal_not_plus_z() -> None:
    points = np.array(
        [
            [1.0, 0.0, 0.0],
            [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, 0.0, -1.0],
        ]
    )
    direction, _up = view_direction_for_content(points)
    diagonal = np.array([1.0, 1.0, 0.8])
    diagonal /= np.linalg.norm(diagonal)

    assert direction == pytest.approx(diagonal)
    assert abs(float(direction[2])) < 0.7


def test_general_zoom_and_framing_margin_change_scale_not_direction() -> None:
    structure, _cube = _pz_orbital()
    base = _camera_spec(structure, _camera_args(zoom=1.0), display="unit_cell")
    zoomed = _camera_spec(structure, _camera_args(zoom=2.0), display="unit_cell")
    wider = _camera_spec(
        structure,
        _camera_args(zoom=0.5, framing_margin=2.24),
        display="unit_cell",
    )

    assert zoomed.ortho_scale == pytest.approx(base.ortho_scale / 2.0)
    assert _direction(zoomed) == pytest.approx(_direction(base))
    assert zoomed.target == pytest.approx(base.target)
    assert wider.ortho_scale == pytest.approx(base.ortho_scale * 4.0)
    assert _direction(wider) == pytest.approx(_direction(base))


def test_explicit_target_and_ortho_scale_are_not_replaced() -> None:
    structure, _cube = _pz_orbital()
    camera = _camera_spec(
        structure,
        _camera_args(camera_target=[1.25, -0.5, 0.25], ortho_scale=4.5, zoom=2.0),
        display="unit_cell",
    )

    assert camera.target == pytest.approx([1.25, -0.5, 0.25])
    assert camera.ortho_scale == pytest.approx(4.5)


def test_structure_zoom_scales_the_existing_orthographic_fit() -> None:
    bundle = SimpleNamespace(
        M=np.diag([3.0, 4.0, 5.0]),
        scene={
            "bounds": {
                "center": [0.0, 0.0, 0.0],
                "mins": [-1.0, -1.0, -1.0],
                "maxs": [1.0, 1.0, 1.0],
            }
        },
    )
    structure = SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))
    base = _camera_spec(
        structure,
        _camera_args(show_unit_cell=False, zoom=1.0),
        display="formula_unit",
    )
    zoomed = _camera_spec(
        structure,
        _camera_args(show_unit_cell=False, zoom=2.0),
        display="formula_unit",
    )

    assert zoomed.ortho_scale == pytest.approx(base.ortho_scale / 2.0)
    assert _direction(zoomed) == pytest.approx(_direction(base))


def test_cpu_fit_ignores_a_drawn_cell_and_does_not_look_down_z() -> None:
    _structure, cube = _pz_orbital()
    meshes = cube_isosurface_meshes(cube, isovalue=0.45, stride=1)
    scene = {
        "atoms": [
            {
                "elem": "O",
                "cart": np.array([3.6, 3.6, 3.6]),
                "atom_radius": 0.55,
                "label": "O1",
            }
        ],
        "bonds": [],
        "matrix": cube.lattice,
        "isosurfaces": meshes,
        "display_mode": "unit_cell",
    }
    plan = prepare_render(
        scene,
        render={"show_cell": True, "width": 160, "height": 160, "representation": "ball_stick"},
    )
    camera = plan.camera
    cell = unit_cell_primitive("unit-cell", cube.lattice)
    cell_radius = float(
        np.linalg.norm(
            cell.segments.reshape(-1, 3) - np.asarray(camera.target),
            axis=1,
        ).max()
    )

    assert camera.ortho_scale < cell_radius * 0.45
    assert abs(float(np.dot(_direction(camera), [0.0, 0.0, 1.0]))) < 0.45
    assert any(
        isinstance(primitive, LinePrimitive) and primitive.semantic_id == "unit-cell"
        for primitive in plan.primitives
    )
    assert any(isinstance(primitive, TriangleMeshPrimitive) for primitive in plan.primitives)


def test_cpu_render_of_a_pz_pair_shows_a_waist_rather_than_a_speck_or_disk() -> None:
    from mat_viewer.render.cpu.raster import render_rgba

    _structure, cube = _pz_orbital()
    meshes = cube_isosurface_meshes(cube, isovalue=0.45, stride=1)
    scene = {
        "atoms": [
            {
                "elem": "O",
                "cart": np.array([3.6, 3.6, 3.6]),
                "atom_radius": 0.4,
                "label": "O1",
            }
        ],
        "bonds": [],
        "matrix": np.eye(3) * 40.0,
        "isosurfaces": meshes,
    }
    plan = prepare_render(
        scene,
        render={
            "show_cell": True,
            "width": 180,
            "height": 180,
            "background": (1.0, 1.0, 1.0, 1.0),
        },
    )
    image = render_rgba(plan)
    foreground = np.any(image[:, :, :3] < 250, axis=2)
    assert foreground.mean() > 0.08

    rows = np.flatnonzero(foreground.any(axis=1))
    cols = np.flatnonzero(foreground.any(axis=0))
    assert rows.size and cols.size
    row_band = foreground[rows[0] : rows[-1] + 1]
    third = max(len(row_band) // 3, 1)
    top = int(row_band[:third].sum())
    middle = int(row_band[third : 2 * third].sum())
    bottom = int(row_band[2 * third :].sum())
    assert middle < 0.85 * min(top, bottom)

    orange = (image[:, :, 0] > image[:, :, 2] + 20) & foreground
    blue = (image[:, :, 2] > image[:, :, 0] + 20) & foreground
    assert int(orange.sum()) > 20
    assert int(blue.sum()) > 20


def test_content_view_resolver_pins_scale_and_direction_precedence() -> None:
    lobe_a = np.stack(
        (
            np.linspace(-3.2, -2.2, 12),
            np.zeros(12),
            np.linspace(-0.2, 0.2, 12),
        ),
        axis=1,
    )
    lobe_b = lobe_a.copy()
    lobe_b[:, 0] *= -1.0
    both = np.vstack((lobe_a, lobe_b))
    base = resolve_content_view(both, width=200, height=100)
    zoomed = resolve_content_view(both, width=200, height=100, zoom=2.0)
    wider = resolve_content_view(both, width=200, height=100, framing_margin=2.24)
    pinned = resolve_content_view(
        both, width=200, height=100, ortho_scale=7.5, zoom=4.0
    )
    directed = resolve_content_view(
        both, width=200, height=100, view_direction=[0.0, 0.0, 1.0], zoom=3.0
    )
    lattice = resolve_content_view(both, width=200, height=100, camera_axis="c*")
    kept = resolve_content_view(lobe_a, width=200, height=100)

    assert zoomed["ortho_scale"] == pytest.approx(base["ortho_scale"] / 2.0)
    assert zoomed["direction"] == pytest.approx(base["direction"])
    assert wider["ortho_scale"] == pytest.approx(base["ortho_scale"] * 2.0)
    assert pinned["ortho_scale"] == pytest.approx(7.5)
    assert pinned["scale_authority"] == "ortho_scale"
    assert pinned["direction"] == pytest.approx(base["direction"])
    assert directed["authority"] == "view_direction"
    assert directed["direction"] == pytest.approx([0.0, 0.0, 1.0])
    assert lattice["authority"] == "camera_axis"
    assert lattice["direction"] is None
    assert not np.allclose(kept["target"], base["target"])
    assert kept["ortho_scale"] < base["ortho_scale"]


def test_empty_stored_isosurface_is_not_replaced_by_the_grid(monkeypatch) -> None:
    structure, _cube, atom = _padded_orbital()
    scene = structure.frames[0].bundle.scene
    scene["isosurfaces"] = []

    def _must_not_march(*_args, **_kwargs):
        raise AssertionError("stored isosurface list must not be remeshed")

    monkeypatch.setattr(
        "mat_viewer.cube.cpu.cube_isosurface_meshes", _must_not_march
    )
    vertices = isosurface_mesh_vertices(scene, {"isosurface_enabled": True})
    assert len(vertices) == 0
    camera = _camera_spec(
        structure, _camera_args(show_unit_cell=True), display="unit_cell"
    )
    assert scene["isosurfaces"] == []
    assert np.linalg.norm(np.asarray(camera.target) - atom) < 1.6

    monkeypatch.undo()
    full = _padded_orbital()
    full_camera = _camera_spec(
        full[0], _camera_args(show_unit_cell=True), display="unit_cell"
    )
    assert camera.ortho_scale < full_camera.ortho_scale


def test_camera_request_reports_zoom_and_framing_margin() -> None:
    from mat_viewer.render.cli_controls import _camera_request

    payload = _camera_request(
        Namespace(
            projection="orthographic",
            camera_axis=None,
            view_direction=None,
            camera_position=None,
            camera_target=None,
            camera_up=None,
            camera_distance=1.8,
            field_of_view=None,
            ortho_scale=None,
            camera_clip=None,
            zoom=2.0,
            framing_margin=1.4,
        )
    )

    assert payload["zoom"] == 2.0
    assert payload["framing_margin"] == 1.4


def _lobe(centre: np.ndarray) -> np.ndarray:
    corners = np.asarray(
        [[x, y, z] for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)],
        dtype=float,
    )
    return np.asarray(centre, dtype=float) + (corners - 0.5) * np.array([1.6, 0.5, 0.35])


def _stored_mesh_structure(parts: tuple[np.ndarray, ...]) -> SimpleNamespace:
    values = np.zeros((4, 4, 4))
    values[1, 1, 1] = 1.0
    cube = _field_cube(values, spacing=12.0, atom_index=(1.5, 1.5, 1.5))
    structure = _structure(cube, atom_cart=np.zeros(3))
    scene = structure.frames[0].bundle.scene
    scene["draw_atoms"] = []
    scene["isosurfaces"] = [{"vertices": np.asarray(part, dtype=float)} for part in parts]
    return structure


def _content_bounds(parts: tuple[np.ndarray, ...]) -> tuple[np.ndarray, np.ndarray, float]:
    vertices = np.vstack(parts)
    minimum = vertices.min(axis=0)
    maximum = vertices.max(axis=0)
    target = 0.5 * (minimum + maximum)
    radius = max(float(np.linalg.norm(0.5 * (maximum - minimum))), 0.5)
    return vertices, target, radius


def test_removed_lobe_reframes_perspective_and_orthographic(monkeypatch) -> None:
    """A stored mesh is the final geometry for both projections.

    The second lobe stands in for a mask or clip that already deleted those
    vertices. The large cell must not set the target, the orthographic
    half-height, or the perspective distance.
    """

    def _must_not_march(*_args, **_kwargs):
        raise AssertionError("stored isosurface list must not be remeshed")

    monkeypatch.setattr("mat_viewer.cube.cpu.cube_isosurface_meshes", _must_not_march)
    kept_lobe = _lobe(np.array([5.0, 0.2, -0.1]))
    removed_lobe = _lobe(np.array([-5.0, -0.3, 0.15]))
    both = (kept_lobe, removed_lobe)
    kept = (kept_lobe,)
    full_vertices, full_target, full_radius = _content_bounds(both)
    kept_vertices, kept_target, kept_radius = _content_bounds(kept)
    assert kept_radius < full_radius * 0.5
    assert abs(float(full_target[0])) < 0.2
    assert float(kept_target[0]) > 4.0

    cameras = {}
    for label, parts in (("full", both), ("kept", kept)):
        structure = _stored_mesh_structure(parts)
        cell_center = np.asarray(structure.frames[0].bundle.scene["bounds"]["center"], dtype=float)
        assert np.linalg.norm(cell_center) > 15.0
        for projection in ("orthographic", "perspective"):
            camera = _camera_spec(
                structure,
                _camera_args(show_unit_cell=True, projection=projection),
                display="unit_cell",
            )
            cameras[label, projection] = camera
            assert np.allclose(camera.target, full_target if label == "full" else kept_target)
            assert np.linalg.norm(np.asarray(camera.target) - cell_center) > 10.0

    for label, vertices, target, radius in (
        ("full", full_vertices, full_target, full_radius),
        ("kept", kept_vertices, kept_target, kept_radius),
    ):
        ortho = cameras[label, "orthographic"]
        perspective = cameras[label, "perspective"]
        assert np.allclose(ortho.target, perspective.target)
        direction = np.asarray(ortho.position) - np.asarray(ortho.target)
        expected_scale = orthographic_scale_for_points(
            vertices,
            np.zeros(len(vertices), dtype=float),
            target,
            direction,
            np.asarray(ortho.up),
            aspect=900.0 / 720.0,
            zoom=1.0,
            framing_margin=1.12,
        )
        assert ortho.ortho_scale == pytest.approx(expected_scale)
        expected_distance = 1.8 * radius / np.sin(np.radians(22.5))
        assert np.linalg.norm(
            np.asarray(perspective.position) - np.asarray(perspective.target)
        ) == pytest.approx(expected_distance)

    assert cameras["kept", "orthographic"].ortho_scale < cameras["full", "orthographic"].ortho_scale
    kept_distance = np.linalg.norm(
        np.asarray(cameras["kept", "perspective"].position)
        - np.asarray(cameras["kept", "perspective"].target)
    )
    full_distance = np.linalg.norm(
        np.asarray(cameras["full", "perspective"].position)
        - np.asarray(cameras["full", "perspective"].target)
    )
    assert kept_distance / full_distance == pytest.approx(kept_radius / full_radius)
    assert kept_distance < full_distance
