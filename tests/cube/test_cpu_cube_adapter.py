from __future__ import annotations

import argparse
import builtins
import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.cli import main
from mat_viewer.cube.color_field import _sample_trilinear, colormap_rgb
from mat_viewer.cube.cpu import cube_isosurface_meshes, ensure_cube_isosurfaces
from mat_viewer.cube.io import CubeAtom, CubeData
from mat_viewer.render.contracts import LinePrimitive, RenderSpec, TriangleMeshPrimitive
from mat_viewer.render.geometry import triangle_mesh_edge_segments
from mat_viewer.render.mesh_overlays import isosurface_primitives


def _cube(tmp_path: Path) -> CubeData:
    coordinates = np.indices((7, 7, 7), dtype=float)
    radius = np.sqrt(sum((coordinates[axis] - 3.0) ** 2 for axis in range(3)))
    values = 2.0 - radius
    return CubeData(
        title="test",
        comment="",
        atoms=[],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.2,
        values=values,
        path=tmp_path / "test.cube",
    )


def test_cube_extra_produces_backend_neutral_positive_and_negative_meshes(
    tmp_path: Path,
) -> None:
    meshes = cube_isosurface_meshes(_cube(tmp_path), isovalue=0.5)

    assert {mesh["id"] for mesh in meshes} == {"cube:positive", "cube:negative"}
    for mesh in meshes:
        assert mesh["vertices"].ndim == 2
        assert mesh["vertices"].shape[1] == 3
        assert mesh["triangles"].ndim == 2
        assert mesh["triangles"].shape[1] == 3
        assert mesh["normals"].shape == mesh["vertices"].shape


def test_cube_mesh_parameters_are_explicitly_applied(tmp_path: Path) -> None:
    meshes = cube_isosurface_meshes(
        _cube(tmp_path), isovalue=0.5, positive_color="#C97A50",
        negative_color="#4A90C4", opacity=0.30, stride=2,
        ambient=0.45, diffuse=0.50,
    )

    assert {mesh["color"] for mesh in meshes} <= {"#C97A50", "#4A90C4"}
    assert {mesh["opacity"] for mesh in meshes} == {0.30}
    assert {
        (mesh["metadata"]["material"]["ambient"],
         mesh["metadata"]["material"]["diffuse"])
        for mesh in meshes
    } == {(0.45, 0.50)}


def test_triangle_mesh_edges_are_unique_but_keep_diagonals() -> None:
    vertices = np.asarray(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float
    )
    triangles = np.asarray([[0, 1, 2], [0, 2, 3]], dtype=np.int64)

    segments = triangle_mesh_edge_segments(vertices, triangles)

    assert segments.shape == (5, 2, 3)
    assert {
        tuple(sorted((tuple(start), tuple(end)))) for start, end in segments
    } == {
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
        ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((1.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
        ((0.0, 1.0, 0.0), (1.0, 1.0, 0.0)),
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("isosurface_mode", "outline", "isosurface_mode"),
        ("isosurface_wireframe_width", 0.0, "wireframe_width"),
        ("isosurface_wireframe_opacity", 1.1, "wireframe_opacity"),
    ],
)
def test_wireframe_render_spec_validates_controls(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RenderSpec(**{field: value})


def test_wireframe_isosurface_compiles_to_colored_line_primitives() -> None:
    scene = {
        "isosurfaces": [
            {
                "id": "cube:positive",
                "phase": "positive",
                "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                "triangles": [[0, 1, 2]],
                "color": "#D55E00",
                "opacity": 0.55,
            },
            {
                "id": "cube:negative",
                "phase": "negative",
                "vertices": [[0, 0, 1], [1, 0, 1], [0, 1, 1]],
                "triangles": [[0, 1, 2]],
                "color": "#0072B2",
                "opacity": 0.55,
            },
        ]
    }

    primitives, warnings = isosurface_primitives(
        scene,
        render_spec=RenderSpec(
            isosurface_mode="wireframe",
            isosurface_wireframe_width=1.25,
            isosurface_wireframe_opacity=0.8,
        ),
    )

    assert not warnings
    assert all(isinstance(primitive, LinePrimitive) for primitive in primitives)
    assert not any(
        isinstance(primitive, TriangleMeshPrimitive) for primitive in primitives
    )
    assert [primitive.segments.shape for primitive in primitives] == [(3, 2, 3)] * 2
    assert [primitive.width_px for primitive in primitives] == [1.25, 1.25]
    assert [primitive.rgba[3] for primitive in primitives] == [0.8, 0.8]
    assert [primitive.rgba[:3] for primitive in primitives] == [
        (213 / 255, 94 / 255, 0.0),
        (0.0, 114 / 255, 178 / 255),
    ]


def _with_color(
    cube: CubeData,
    values: np.ndarray,
    *,
    origin: np.ndarray | None = None,
    axes: np.ndarray | None = None,
) -> CubeData:
    return CubeData(
        title="color",
        comment="",
        atoms=[],
        origin=np.array(cube.origin if origin is None else origin, dtype=float),
        axes=np.array(cube.axes if axes is None else axes, dtype=float),
        values=np.asarray(values, dtype=float),
        path=cube.path.with_name("color.cube"),
    )


def test_color_cube_assigns_distinct_vertex_colors(tmp_path: Path) -> None:
    cube = _cube(tmp_path)
    color_values = np.linspace(-1.0, 1.0, cube.values.size).reshape(cube.values.shape)

    meshes = cube_isosurface_meshes(
        cube,
        isovalue=0.5,
        color_cube=_with_color(cube, color_values),
        color_range=(-1.0, 1.0),
        colormap="bgr",
    )

    colors = np.vstack([mesh["vertex_colors"] for mesh in meshes])
    assert colors.shape[1] == 3
    assert float(colors.max() - colors.min()) > 0.2


def test_color_center_puts_zero_on_green(tmp_path: Path) -> None:
    cube = _cube(tmp_path)
    flat = np.zeros(cube.values.shape)
    flat.ravel()[:] = 0.0

    meshes = cube_isosurface_meshes(
        cube,
        isovalue=0.5,
        color_cube=_with_color(cube, flat),
        color_range=(-0.04, 0.02),
        color_center=0.0,
        colormap="bgr",
    )

    color = meshes[0]["vertex_colors"][0]
    assert color[0] < 0.05
    assert color[1] > 0.95
    assert color[2] < 0.05


def test_color_cube_rejects_a_grid_that_does_not_cover_the_surface(
    tmp_path: Path,
) -> None:
    source = _cube(tmp_path)
    with pytest.raises(ValueError, match="does not cover"):
        cube_isosurface_meshes(
            source,
            isovalue=0.5,
            color_cube=_with_color(source, np.zeros((2, 2, 2))),
        )


def test_cube_mesh_material_coefficients_are_validated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"ambient \+ diffuse"):
        cube_isosurface_meshes(
            _cube(tmp_path), isovalue=0.5, ambient=0.7, diffuse=0.4,
        )

def test_cube_meshes_are_attached_to_the_loaded_scene(tmp_path: Path) -> None:
    @dataclass
    class Bundle:
        cube_data: CubeData
        scene: dict
        scene_cache: dict

    @dataclass
    class Frame:
        bundle: Bundle

    @dataclass
    class Structure:
        frames: tuple[Frame, ...]
        input_format: str = "cube"

    scene: dict = {}
    bundle = Bundle(_cube(tmp_path), scene, {("unit_cell", False): scene})
    structure = Structure((Frame(bundle),))

    assert ensure_cube_isosurfaces(structure) is structure
    assert scene["isosurfaces"]
    assert bundle.cube_data.surface_meshes is scene["isosurfaces"]


def _write_cube(path: Path, values: np.ndarray | None = None) -> None:
    if values is None:
        coordinates = np.indices((7, 7, 7), dtype=float)
        radius = np.sqrt(sum((coordinates[axis] - 3.0) ** 2 for axis in range(3)))
        values = 2.0 - radius
    values = np.asarray(values, dtype=float).ravel()
    value_lines = [
        " ".join(f"{value:.8e}" for value in values[index : index + 6])
        for index in range(0, len(values), 6)
    ]
    path.write_text(
        "\n".join(
            [
                "MatterVis cube integration",
                "positive and negative radial field",
                "1 0.0 0.0 0.0",
                "7 0.4 0.0 0.0",
                "7 0.0 0.4 0.0",
                "7 0.0 0.0 0.4",
                "6 0.0 1.2 1.2 1.2",
                *value_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_cube_cli_preflight_and_cpu_render_are_plotly_free(
    tmp_path: Path, capsys
) -> None:
    pytest.importorskip("skimage")
    source = tmp_path / "orbital.cube"
    output = tmp_path / "orbital.png"
    _write_cube(source)

    import mat_viewer.capabilities as capability_module

    if capability_module.molcryskit_contract_missing():
        pytest.skip("requires the exact MolCrysKit structure-contract commit")

    main(
        [
            "render",
            str(source),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-cell",
            "--width",
            "96",
            "--height",
            "96",
            "--scale",
            "1",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert payload["backend"] == "cpu"
    assert payload["source"]["input_format"] == "cube"
    assert payload["install"] == 'python -m pip install "matter-vis[cube]"'


def test_explicit_legacy_cube_trace_fails_when_cube_extra_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    from mat_viewer.render.traces_isosurface import isosurface_overlay_traces

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("skimage"):
            raise ImportError("test blocked scikit-image")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(ImportError, match=r"matter-vis\[cube\]"):
        isosurface_overlay_traces(
            {"cube_data": _cube(tmp_path)},
            {"isosurface_enabled": True},
        )


def test_legacy_plotly_wireframe_trace_uses_lines_without_mesh(
    tmp_path: Path,
) -> None:
    pytest.importorskip("plotly")
    from mat_viewer.render.traces_isosurface import isosurface_overlay_traces

    traces = isosurface_overlay_traces(
        {"cube_data": _cube(tmp_path)},
        {
            "isosurface_enabled": True,
            "isosurface_isovalue": 0.5,
            "isosurface_mode": "wireframe",
            "isosurface_wireframe_width": 1.25,
            "isosurface_wireframe_opacity": 0.8,
        },
    )

    assert traces
    assert {trace["type"] for trace in traces} == {"scatter3d"}
    assert all(trace["mode"] == "lines" for trace in traces)
    assert all(trace["line"]["width"] == 1.25 for trace in traces)
    assert all(trace["opacity"] == 0.8 for trace in traces)


def test_public_cube_facade_excludes_bond_reinference_builders() -> None:
    import mat_viewer.cube as cube_module

    removed = {"bond_traces", "build_orbital_figure", "build_orbital_panel_figure"}
    assert removed.isdisjoint(cube_module.__all__)
    for name in removed:
        assert not hasattr(cube_module, name)


def test_cube_structure_bridge_preserves_mck_boundary_bond(tmp_path: Path) -> None:
    from mat_viewer.loader.cube_adapter import build_loaded_crystal_from_cube

    cube = CubeData(
        title="periodic bond",
        comment="canonical MCK bond contract",
        atoms=[
            CubeAtom(6, 0.0, np.asarray([9.8, 5.0, 5.0])),
            CubeAtom(6, 0.0, np.asarray([0.2, 5.0, 5.0])),
        ],
        origin=np.zeros(3),
        axes=np.eye(3),
        values=np.zeros((10, 10, 10)),
        path=tmp_path / "periodic.cube",
    )

    bundle = build_loaded_crystal_from_cube(cube)

    assert bundle.molcrys_analysis.bond_pairs == [(0, 1)]
    assert bundle.molcrys_analysis.bond_records[0]["right_image_shift"] == [1, 0, 0]
    assert bundle.scene["bonds"][0]["end"][0] == pytest.approx(10.2)

def test_cube_meshes_keep_the_scene_origin_frame_for_nonzero_cube_origin(tmp_path: Path) -> None:
    cube = _cube(tmp_path)
    cube.origin = np.array([5.0, 6.0, 7.0])

    meshes = cube_isosurface_meshes(cube, isovalue=0.5)

    vertices = np.concatenate([mesh["vertices"] for mesh in meshes])
    assert float(vertices.min()) >= -1.0e-8
    assert float(vertices.max()) <= 6.0 * 0.2 + 1.0e-8


def test_color_cube_follows_physical_origin_and_axes(tmp_path: Path) -> None:
    source = _cube(tmp_path)
    values = np.broadcast_to(
        np.arange(source.values.shape[0], dtype=float)[:, None, None],
        source.values.shape,
    ).copy()
    shifted = _with_color(source, values, origin=source.origin + 0.25 * source.axes[0])

    meshes = cube_isosurface_meshes(
        source,
        isovalue=0.5,
        stride=1,
        color_cube=shifted,
        color_range=(0.0, 6.0),
    )

    world = meshes[0]["vertices"]
    source_index = world @ np.linalg.inv(source.axes)
    physical = colormap_rgb("bgr", source_index[:, 0] - 0.25, 0.0, 6.0)
    paired_by_index = colormap_rgb("bgr", source_index[:, 0], 0.0, 6.0)
    assert np.allclose(meshes[0]["vertex_colors"], physical, atol=1e-5)
    assert not np.allclose(meshes[0]["vertex_colors"], paired_by_index, atol=1e-3)


def test_color_cube_rejects_a_same_shape_grid_outside_the_surface(tmp_path: Path) -> None:
    source = _cube(tmp_path)
    source.origin = np.array([1.0, 0.0, 0.0])
    color = _with_color(
        source, np.zeros(source.values.shape), origin=np.zeros(3)
    )

    with pytest.raises(ValueError, match="origin"):
        cube_isosurface_meshes(source, isovalue=0.5, color_cube=color)


def test_color_cube_samples_a_finer_grid_at_the_same_physical_volume(
    tmp_path: Path,
) -> None:
    source = _cube(tmp_path)
    shape = (15, 15, 15)
    physical_x = np.indices(shape, dtype=float)[0] * 0.1
    color = _with_color(
        source, physical_x, origin=np.zeros(3), axes=np.eye(3) * 0.1
    )

    meshes = cube_isosurface_meshes(source, isovalue=0.5, stride=1, color_cube=color)

    expected = colormap_rgb("bgr", meshes[0]["vertices"][:, 0], 0.0, 1.4)
    assert np.allclose(meshes[0]["vertex_colors"], expected, atol=1e-5)


def test_color_sampling_keeps_odd_voxels_when_the_mesh_stride_drops_them(
    tmp_path: Path,
) -> None:
    source = _cube(tmp_path)
    field = np.zeros(source.values.shape)
    field[1::2] = 1.0

    meshes = cube_isosurface_meshes(
        source,
        isovalue=0.5,
        stride=2,
        color_cube=_with_color(source, field),
        color_range=(0.0, 1.0),
    )

    colors = np.vstack([mesh["vertex_colors"] for mesh in meshes])
    assert float(colors[:, 0].max()) > 0.2


def test_inferred_color_range_uses_the_full_color_grid(tmp_path: Path) -> None:
    source = _cube(tmp_path)
    field = np.zeros(source.values.shape)
    field[1, 0, 0] = 5.0

    meshes = cube_isosurface_meshes(
        source,
        isovalue=0.5,
        stride=2,
        color_cube=_with_color(source, field),
    )

    colors = meshes[0]["vertex_colors"]
    assert float(colors[:, 2].min()) > 0.9
    assert float(colors[:, 1].max()) < 0.1


def test_constant_color_cube_maps_to_the_colormap_midpoint(tmp_path: Path) -> None:
    source = _cube(tmp_path)
    meshes = cube_isosurface_meshes(
        source,
        isovalue=0.5,
        color_cube=_with_color(source, np.full(source.values.shape, 3.0)),
    )

    color = meshes[0]["vertex_colors"]
    assert np.allclose(color, color[0])
    assert color[0, 0] < 0.05
    assert color[0, 1] > 0.95
    assert color[0, 2] < 0.05


def test_nonfinite_color_voxels_away_from_the_surface_are_ignored(
    tmp_path: Path,
) -> None:
    source = _cube(tmp_path)
    field = np.full(source.values.shape, 3.0)
    field[0, 0, 0] = np.nan

    meshes = cube_isosurface_meshes(
        source, isovalue=0.5, color_cube=_with_color(source, field)
    )

    assert meshes[0]["vertex_colors"][0, 1] > 0.95


def test_exact_grid_point_ignores_a_zero_weight_nonfinite_corner() -> None:
    grid = np.zeros((2, 2, 2))
    grid[1, 1, 1] = np.nan

    sampled = _sample_trilinear(grid, np.asarray([[0.0, 0.0, 0.0]]))

    assert sampled[0] == pytest.approx(0.0)


def test_grid_face_ignores_zero_weight_nonfinite_voxels() -> None:
    grid = np.zeros((2, 2, 2))
    grid[:, :, 0] = np.asarray([[0.0, 4.0], [2.0, 6.0]])
    grid[:, :, 1] = np.nan

    sampled = _sample_trilinear(grid, np.asarray([[0.5, 0.5, 0.0]]))

    assert sampled[0] == pytest.approx(3.0)


def test_nonzero_weight_on_a_nonfinite_corner_stays_nonfinite() -> None:
    grid = np.zeros((2, 2, 2))
    grid[1, 0, 0] = np.nan

    sampled = _sample_trilinear(grid, np.asarray([[0.5, 0.0, 0.0]]))

    assert not np.isfinite(sampled[0])


def test_nonfinite_color_samples_are_an_error(tmp_path: Path) -> None:
    source = _cube(tmp_path)
    field = np.zeros(source.values.shape)
    field[3] = np.nan

    with pytest.raises(ValueError, match="nonfinite"):
        cube_isosurface_meshes(
            source, isovalue=0.5, color_cube=_with_color(source, field)
        )


def test_color_cube_does_not_move_the_uncolored_mesh(tmp_path: Path) -> None:
    source = _cube(tmp_path)
    plain = cube_isosurface_meshes(source, isovalue=0.5, stride=2)
    colored = cube_isosurface_meshes(
        source,
        isovalue=0.5,
        stride=2,
        color_cube=_with_color(source, np.linspace(-1.0, 1.0, source.values.size).reshape(source.values.shape)),
        color_range=(-1.0, 1.0),
    )

    assert [mesh["id"] for mesh in plain] == [mesh["id"] for mesh in colored]
    for uncolored, painted in zip(plain, colored):
        assert np.array_equal(uncolored["vertices"], painted["vertices"])
        assert np.array_equal(uncolored["triangles"], painted["triangles"])
        assert "vertex_colors" not in uncolored
        assert painted["vertex_colors"].shape[0] == painted["vertices"].shape[0]


def test_wireframe_carries_sampled_endpoint_colors() -> None:
    primitives, warnings = isosurface_primitives(
        {
            "isosurfaces": [
                {
                    "id": "cube:positive",
                    "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                    "triangles": [[0, 1, 2]],
                    "color": "#D55E00",
                    "opacity": 0.55,
                    "vertex_colors": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                }
            ]
        },
        render_spec=RenderSpec(isosurface_mode="wireframe"),
    )

    assert not warnings
    assert isinstance(primitives[0], LinePrimitive)
    assert primitives[0].endpoint_colors is not None
    assert primitives[0].endpoint_colors.shape[1:] == (2, 3)
    assert float(primitives[0].endpoint_colors.max()) > 0.9
    assert float(primitives[0].endpoint_colors.min()) < 0.1


def test_color_settings_replace_a_cached_isosurface(tmp_path: Path) -> None:
    @dataclass
    class Bundle:
        cube_data: CubeData
        scene: dict
        scene_cache: dict

    @dataclass
    class Frame:
        bundle: Bundle

    @dataclass
    class Structure:
        frames: tuple[Frame, ...]

    scene: dict = {}
    cube = _cube(tmp_path)
    bundle = Bundle(cube, scene, {("unit_cell", False): scene})
    structure = Structure((Frame(bundle),))
    ensure_cube_isosurfaces(structure, isovalue=0.5, stride=1)
    uncolored = scene["isosurfaces"]
    assert "vertex_colors" not in uncolored[0]

    ensure_cube_isosurfaces(
        structure,
        isovalue=0.5,
        stride=1,
        color_cube=_with_color(cube, np.linspace(-1.0, 1.0, cube.values.size).reshape(cube.values.shape)),
        color_range=(-1.0, 1.0),
    )
    colored = scene["isosurfaces"]
    assert colored is not uncolored
    assert "vertex_colors" in colored[0]
    assert bundle.cube_data.surface_meshes is colored

    ensure_cube_isosurfaces(
        structure,
        isovalue=0.5,
        stride=1,
        color_cube=_with_color(cube, np.linspace(-1.0, 1.0, cube.values.size).reshape(cube.values.shape)),
        color_range=(-1.0, 1.0),
    )
    assert scene["isosurfaces"] is colored


def _isosurface_primitives(plan):
    return [
        primitive
        for primitive in plan.primitives
        if primitive.semantic_id.startswith("isosurface:")
    ]


def test_cli_color_cube_reaches_the_cpu_render(tmp_path: Path, capsys) -> None:
    pytest.importorskip("skimage")
    import mat_viewer.capabilities as capability_module

    if capability_module.molcryskit_contract_missing():
        pytest.skip("requires the exact MolCrysKit structure-contract commit")

    from mat_viewer.agent import load_structure, prepare_render
    from mat_viewer.cli import _build_render_parser
    from mat_viewer.render.cli_controls import isosurface_render_fields
    from mat_viewer.render.cpu import render_rgba

    source = tmp_path / "orbital.cube"
    color = tmp_path / "color.cube"
    plain_output = tmp_path / "plain.png"
    colored_output = tmp_path / "colored.png"
    _write_cube(source)
    ramp = np.linspace(-1.0, 1.0, 7 * 7 * 7).reshape(7, 7, 7)
    _write_cube(color, ramp)
    parser = argparse.ArgumentParser()
    render_parser = _build_render_parser(parser.add_subparsers())
    colored_args = render_parser.parse_args(
        [
            str(source),
            "-o",
            str(colored_output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-cell",
            "--width",
            "64",
            "--height",
            "64",
            "--scale",
            "1",
            "--isovalue",
            "0.5",
            "--isosurface-stride",
            "1",
            "--isosurface-color-cube",
            str(color),
            "--isosurface-color-range",
            "-1",
            "1",
            "--isosurface-colormap",
            "bgr",
        ]
    )
    fields = isosurface_render_fields(colored_args)
    assert fields["isosurface_color_cube"] == str(color)
    loaded = load_structure(source)
    plain_spec = RenderSpec(
        width=64,
        height=64,
        scale=1,
        backend="cpu",
        show_cell=False,
        isosurface_isovalue=0.5,
        isosurface_stride=1,
    )
    colored_spec = replace(plain_spec, **fields)
    plain_plan = prepare_render(loaded, render_spec=plain_spec)
    colored_plan = prepare_render(loaded, render_spec=colored_spec)
    flat_plan = prepare_render(
        loaded, render_spec=replace(colored_spec, shading="flat")
    )
    wire_plan = prepare_render(
        loaded, render_spec=replace(colored_spec, isosurface_mode="wireframe")
    )
    plain_wire = prepare_render(
        loaded, render_spec=replace(plain_spec, isosurface_mode="wireframe")
    )

    assert all(
        item.vertex_colors is None
        for item in _isosurface_primitives(plain_plan)
        if isinstance(item, TriangleMeshPrimitive)
    )
    colored_meshes = [
        item
        for item in _isosurface_primitives(colored_plan)
        if isinstance(item, TriangleMeshPrimitive)
    ]
    assert colored_meshes
    assert all(item.vertex_colors is not None for item in colored_meshes)
    flat_meshes = [
        item
        for item in _isosurface_primitives(flat_plan)
        if isinstance(item, TriangleMeshPrimitive)
    ]
    assert all(item.vertex_normals is None for item in flat_meshes)
    assert all(item.vertex_colors is not None for item in flat_meshes)
    assert all(
        isinstance(item, LinePrimitive) and item.endpoint_colors is not None
        for item in _isosurface_primitives(wire_plan)
    )
    assert all(
        isinstance(item, LinePrimitive) and item.endpoint_colors is None
        for item in _isosurface_primitives(plain_wire)
    )
    plain_image = render_rgba(plain_plan)
    colored_image = render_rgba(colored_plan)
    flat_image = render_rgba(flat_plan)
    flat_plain = prepare_render(
        loaded, render_spec=replace(plain_spec, shading="flat")
    )
    assert np.array_equal(plain_image, render_rgba(plain_plan))
    assert not np.array_equal(colored_image, plain_image)
    assert not np.array_equal(flat_image, render_rgba(flat_plain))
    assert not np.array_equal(render_rgba(wire_plan), render_rgba(plain_wire))

    main(
        [
            "render",
            str(source),
            "-o",
            str(plain_output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-cell",
            "--width",
            "64",
            "--height",
            "64",
            "--scale",
            "1",
            "--isovalue",
            "0.5",
            "--json",
        ]
    )
    main(
        [
            "render",
            str(source),
            "-o",
            str(colored_output),
            "--backend",
            "cpu",
            "--view",
            "unit_cell",
            "--no-cell",
            "--width",
            "64",
            "--height",
            "64",
            "--scale",
            "1",
            "--isovalue",
            "0.5",
            "--isosurface-color-cube",
            str(color),
            "--isosurface-color-range",
            "-1",
            "1",
            "--json",
        ]
    )
    capsys.readouterr()
    assert plain_output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert colored_output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert plain_output.read_bytes() != colored_output.read_bytes()
