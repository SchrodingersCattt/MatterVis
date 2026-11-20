"""Opt-in density ceiling for interaction isosurfaces."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.cube.cpu import cube_isosurface_meshes, ensure_cube_isosurfaces
from mat_viewer.cube.density_mask import filter_mesh_by_density
from mat_viewer.cube.io import CubeData
from mat_viewer.render.cli_controls import (
    _validate_render_options,
    isosurface_render_fields,
)
from mat_viewer.render.contracts import RenderSpec


def _cube(
    values: np.ndarray,
    *,
    origin: np.ndarray | None = None,
    axes: np.ndarray | None = None,
    name: str = "field.cube",
) -> CubeData:
    return CubeData(
        title=name,
        comment="",
        atoms=[],
        origin=np.zeros(3) if origin is None else np.asarray(origin, dtype=float),
        axes=np.eye(3) if axes is None else np.asarray(axes, dtype=float),
        values=np.asarray(values, dtype=float),
        path=Path(name),
    )


def _interaction_field() -> tuple[CubeData, CubeData, np.ndarray, np.ndarray]:
    """Two wells. Only the first sits in a high-density neighborhood."""
    shape = (21, 21, 21)
    spacing = 0.1
    coords = np.indices(shape, dtype=float) * spacing
    high = np.array([0.5, 1.0, 1.0])
    low = np.array([1.5, 1.0, 1.0])
    radius_high = np.sqrt(sum((coords[axis] - high[axis]) ** 2 for axis in range(3)))
    radius_low = np.sqrt(sum((coords[axis] - low[axis]) ** 2 for axis in range(3)))
    field = 1.0 - 1.6 * np.exp(-((radius_high / 0.22) ** 2)) - 1.6 * np.exp(
        -((radius_low / 0.22) ** 2)
    )
    density = 0.01 + 0.20 * np.exp(-((radius_high / 0.28) ** 2))
    axes = np.eye(3) * spacing
    return (
        _cube(field, axes=axes, name="interaction.cube"),
        _cube(density, axes=axes, name="density.cube"),
        high,
        low,
    )


def _closer_to(vertices: np.ndarray, target: np.ndarray, other: np.ndarray) -> np.ndarray:
    return np.linalg.norm(vertices - target, axis=1) < np.linalg.norm(
        vertices - other, axis=1
    )


def test_vertex_rule_drops_a_triangle_wholly_above_the_cutoff() -> None:
    source = _cube(np.zeros((2, 2, 2)))
    density_values = np.full((2, 2, 2), 0.2)
    indices = np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float)
    assert (
        filter_mesh_by_density(
            source,
            _cube(density_values, name="density.cube"),
            indices,
            np.asarray([[0, 1, 2]], dtype=np.int64),
            (indices,),
            stride=1,
            cutoff=0.05,
        )
        is None
    )


def test_vertex_rule_keeps_only_the_low_density_triangle() -> None:
    source = _cube(np.zeros((2, 2, 2)))
    density_values = np.zeros((2, 2, 2))
    density_values[0, 0, 0] = 0.01
    density_values[1, 0, 0] = 0.05
    density_values[0, 1, 0] = 0.04
    density_values[1, 1, 0] = 0.20
    density_values[1, 1, 1] = 0.20
    density_values[0, 0, 1] = 0.20
    indices = np.asarray(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [1, 1, 0],
            [1, 1, 1],
            [0, 0, 1],
        ],
        dtype=float,
    )
    triangles = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    positions = indices.copy()

    filtered = filter_mesh_by_density(
        source,
        _cube(density_values, name="density.cube"),
        indices,
        triangles,
        (positions,),
        stride=1,
        cutoff=0.05,
    )

    assert filtered is not None
    kept_triangles, (kept_positions,) = filtered
    assert kept_triangles.shape == (1, 3)
    assert np.allclose(kept_positions[:, 0], [0.0, 1.0, 0.0])


def test_crossing_triangle_is_clipped_at_the_cutoff() -> None:
    source = _cube(np.zeros((4, 4, 2)))
    density_values = np.zeros((4, 4, 2))
    density_values[0, 0, 0] = 0.0
    density_values[2, 0, 0] = 10.0
    density_values[0, 2, 0] = 0.0
    indices = np.asarray([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
    coarse = filter_mesh_by_density(
        source,
        _cube(density_values, name="density.cube"),
        indices,
        np.asarray([[0, 1, 2]], dtype=np.int64),
        (indices,),
        stride=1,
        cutoff=5.0,
    )
    assert coarse is not None
    coarse_faces, (coarse_positions,) = coarse
    assert coarse_faces.shape[0] == 2
    assert any(np.allclose(point, [1.0, 0.0, 0.0]) for point in coarse_positions)
    assert not any(np.allclose(point, [2.0, 0.0, 0.0]) for point in coarse_positions)
    # The same edge, split once, still cuts at x = 1. The midpoint sample is
    # ρ = 5 and stays. Whole-triangle deletion would have removed both.
    density_values[1, 0, 0] = 5.0
    fine_indices = np.asarray(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 2.0, 0.0]]
    )
    fine = filter_mesh_by_density(
        source,
        _cube(density_values, name="density.cube"),
        fine_indices,
        np.asarray([[0, 1, 3], [1, 2, 3]], dtype=np.int64),
        (fine_indices,),
        stride=1,
        cutoff=5.0,
    )
    assert fine is not None
    _faces, (fine_positions,) = fine
    assert np.max(fine_positions[:, 0]) == pytest.approx(1.0)
    assert not np.any(np.all(np.isclose(fine_positions, [2.0, 0.0, 0.0]), axis=1))


def test_mask_removes_the_high_density_well_and_keeps_the_other() -> None:
    pytest.importorskip("skimage")
    field, density, high, low = _interaction_field()
    field_values = field.values.copy()
    density_values = density.values.copy()

    field_id = id(field.values)
    density_id = id(density.values)
    plain = cube_isosurface_meshes(field, isovalue=0.5, stride=1)
    masked = cube_isosurface_meshes(
        field,
        isovalue=0.5,
        stride=1,
        density_mask=True,
        density_cube=density,
        density_cutoff=0.05,
    )

    plain_vertices = np.vstack([mesh["vertices"] for mesh in plain])
    masked_vertices = np.vstack([mesh["vertices"] for mesh in masked])
    assert np.any(_closer_to(plain_vertices, high, low))
    assert np.any(_closer_to(plain_vertices, low, high))
    assert np.all(_closer_to(masked_vertices, low, high))
    assert len(masked_vertices) < len(plain_vertices)
    assert id(field.values) == field_id
    assert id(density.values) == density_id
    assert np.array_equal(field.values, field_values)
    assert np.array_equal(density.values, density_values)


def test_omitting_the_mask_leaves_the_mesh_unchanged() -> None:
    pytest.importorskip("skimage")
    field, density, _high, _low = _interaction_field()
    broken = _cube(density.values, axes=np.zeros((3, 3)), name="broken.cube")
    plain = cube_isosurface_meshes(field, isovalue=0.5, stride=1)
    ignored = cube_isosurface_meshes(
        field,
        isovalue=0.5,
        stride=1,
        density_cube=broken,
        density_cutoff=0.05,
    )

    assert [mesh["id"] for mesh in ignored] == [mesh["id"] for mesh in plain]
    for left, right in zip(plain, ignored):
        assert np.array_equal(left["vertices"], right["vertices"])
        assert np.array_equal(left["triangles"], right["triangles"])
        assert "density_mask" not in left["metadata"]
        assert "density_mask" not in right["metadata"]


def test_enabled_mask_requires_a_cube_and_a_positive_cutoff() -> None:
    pytest.importorskip("skimage")
    field, density, _high, _low = _interaction_field()
    with pytest.raises(ValueError, match="cutoff is missing"):
        cube_isosurface_meshes(
            field, isovalue=0.5, density_mask=True, density_cube=density
        )
    with pytest.raises(ValueError, match="cutoff is missing"):
        cube_isosurface_meshes(
            field, isovalue=0.5, density_mask=True, density_cutoff=0.05
        )
    with pytest.raises(ValueError, match="e/Bohr"):
        cube_isosurface_meshes(
            field,
            isovalue=0.5,
            density_mask=True,
            density_cube=density,
            density_cutoff=0.0,
        )
    with pytest.raises(ValueError, match="e/Å"):
        cube_isosurface_meshes(
            field,
            isovalue=0.5,
            density_mask=True,
            density_cube=density,
            density_cutoff=-1.0,
        )


def test_density_grid_that_does_not_cover_the_surface_is_an_error() -> None:
    pytest.importorskip("skimage")
    field, density, _high, _low = _interaction_field()
    shifted = _cube(
        density.values,
        origin=density.origin + np.array([8.0, 0.0, 0.0]),
        axes=density.axes,
        name="shifted.cube",
    )
    with pytest.raises(ValueError, match="does not cover"):
        cube_isosurface_meshes(
            field,
            isovalue=0.5,
            stride=1,
            density_mask=True,
            density_cube=shifted,
            density_cutoff=0.05,
        )


def test_finer_density_grid_is_sampled_in_the_same_cartesian_frame() -> None:
    pytest.importorskip("skimage")
    field, _coarse, high, low = _interaction_field()
    fine_shape = tuple(size * 2 - 1 for size in field.values.shape)
    spacing = 0.05
    coords = np.indices(fine_shape, dtype=float) * spacing
    radius_high = np.sqrt(sum((coords[axis] - high[axis]) ** 2 for axis in range(3)))
    fine_density = _cube(
        0.01 + 0.20 * np.exp(-((radius_high / 0.28) ** 2)),
        axes=np.eye(3) * spacing,
        name="fine-density.cube",
    )

    masked = cube_isosurface_meshes(
        field,
        isovalue=0.5,
        stride=1,
        density_mask=True,
        density_cube=fine_density,
        density_cutoff=0.05,
    )

    vertices = np.vstack([mesh["vertices"] for mesh in masked])
    assert np.all(_closer_to(vertices, low, high))


def test_color_range_is_independent_of_the_density_mask() -> None:
    pytest.importorskip("skimage")
    field, density, _high, low = _interaction_field()
    color_values = np.linspace(-1.0, 1.0, field.values.size).reshape(field.values.shape)
    color = _cube(color_values, axes=field.axes, name="color.cube")
    color_before = color.values.copy()
    common = {
        "isovalue": 0.5,
        "stride": 1,
        "color_cube": color,
        "color_range": (-1.0, 1.0),
        "colormap": "bgr",
    }
    plain = cube_isosurface_meshes(field, **common)
    masked = cube_isosurface_meshes(
        field,
        density_mask=True,
        density_cube=density,
        density_cutoff=0.05,
        **common,
    )

    plain_vertices = np.vstack([mesh["vertices"] for mesh in plain])
    plain_colors = np.vstack([mesh["vertex_colors"] for mesh in plain])
    masked_vertices = np.vstack([mesh["vertices"] for mesh in masked])
    masked_colors = np.vstack([mesh["vertex_colors"] for mesh in masked])
    for vertex, color_row in zip(masked_vertices, masked_colors):
        match = np.where(np.all(np.isclose(plain_vertices, vertex), axis=1))[0]
        assert match.size
        assert np.allclose(plain_colors[match[0]], color_row)
    assert np.all(_closer_to(masked_vertices, low, _high))
    assert np.array_equal(color.values, color_before)


def test_scene_keeps_one_mesh_list_across_cutoffs() -> None:
    pytest.importorskip("skimage")
    field, density, _high, _low = _interaction_field()

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
    bundle = Bundle(field, scene, {("unit_cell", False): scene})
    structure = Structure((Frame(bundle),))
    previous = None
    tokens: list[str] = []
    for cutoff in (0.04, 0.05, 0.06, 0.08, 0.10, 0.12):
        ensure_cube_isosurfaces(
            structure,
            isovalue=0.5,
            stride=1,
            density_mask=True,
            density_cube=density,
            density_cutoff=cutoff,
        )
        if previous is not None:
            assert scene["isosurfaces"] is not previous
        previous = scene["isosurfaces"]
        tokens.append(scene["isosurface_preparation"])
    assert len(set(tokens)) == len(tokens)
    assert bundle.scene_cache[("unit_cell", False)]["isosurfaces"] is scene["isosurfaces"]
    assert bundle.cube_data.surface_meshes is scene["isosurfaces"]
    mesh_lists = [
        value
        for value in scene.values()
        if isinstance(value, list)
        and value
        and isinstance(value[0], dict)
        and "vertices" in value[0]
    ]
    assert mesh_lists == [scene["isosurfaces"]]

    ensure_cube_isosurfaces(
        structure,
        isovalue=0.5,
        stride=1,
        density_cube=density,
        density_cutoff=0.2,
    )
    unmasked = scene["isosurfaces"]
    ensure_cube_isosurfaces(
        structure,
        isovalue=0.5,
        stride=1,
        density_cube=_cube(np.ones_like(density.values), axes=density.axes),
        density_cutoff=9.0,
    )
    assert scene["isosurfaces"] is unmasked


def test_render_spec_and_cli_keep_the_mask_opt_in() -> None:
    spec = RenderSpec()
    assert spec.isosurface_density_mask is False
    assert spec.isosurface_density_cutoff is None
    stored = RenderSpec(
        isosurface_density_mask=False,
        isosurface_density_cube="density.cube",
        isosurface_density_cutoff=0.05,
    )
    assert stored.isosurface_density_mask is False
    with pytest.raises(ValueError, match="not chosen automatically"):
        RenderSpec(isosurface_density_mask=True, isosurface_density_cube="density.cube")
    with pytest.raises(ValueError, match="e/Bohr"):
        RenderSpec(
            isosurface_density_mask=True,
            isosurface_density_cube="density.cube",
            isosurface_density_cutoff=0.0,
        )

    from mat_viewer.cli import _build_render_parser

    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    omitted = render.parse_args(["field.cube", "-o", "figure.png"])
    fields = isosurface_render_fields(omitted)
    assert fields["isosurface_density_mask"] is False
    assert fields["isosurface_density_cutoff"] is None
    _validate_render_options(omitted)

    enabled = render.parse_args(
        [
            "field.cube",
            "-o",
            "figure.png",
            "--isosurface-density-mask",
            "--isosurface-density-cube",
            "density.cube",
            "--isosurface-density-cutoff",
            "0.05",
        ]
    )
    enabled_fields = isosurface_render_fields(enabled)
    assert enabled_fields["isosurface_density_mask"] is True
    assert enabled_fields["isosurface_density_cube"] == "density.cube"
    assert enabled_fields["isosurface_density_cutoff"] == 0.05
    _validate_render_options(enabled)

    missing = render.parse_args(
        ["field.cube", "-o", "figure.png", "--isosurface-density-mask"]
    )
    with pytest.raises(ValueError, match="no cutoff was chosen"):
        _validate_render_options(missing)


def test_periodic_closure_flag_is_rejected_on_the_agent_command() -> None:
    from mat_viewer.cli import _build_render_parser

    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    requested = render.parse_args(
        ["field.cube", "-o", "figure.png", "--periodic-isosurface"]
    )
    with pytest.raises(ValueError, match="not applied by mat-vis render"):
        _validate_render_options(requested)
    explicit_off = render.parse_args(
        ["field.cube", "-o", "figure.png", "--no-periodic-isosurface"]
    )
    _validate_render_options(explicit_off)


def test_batch_renderer_does_not_drop_the_density_mask() -> None:
    from types import SimpleNamespace

    from mat_viewer.render.fast_cli import render_batch_if_selected

    skipped = render_batch_if_selected(
        SimpleNamespace(
            renderer="auto",
            bond_scale=None,
            bond_threshold_specs=(),
            cell_overlays=None,
            isosurface_density_mask=True,
        ),
        install_command="unused",
    )
    assert skipped is None
    with pytest.raises(ValueError, match="isosurface-density-mask"):
        render_batch_if_selected(
            SimpleNamespace(
                renderer="batch",
                bond_scale=None,
                bond_threshold_specs=(),
                cell_overlays=None,
                isosurface_density_mask=True,
            ),
            install_command="unused",
        )


def test_prepare_render_ignores_a_stored_density_cube_until_the_mask_is_on(
    tmp_path: Path,
) -> None:
    pytest.importorskip("skimage")
    import mat_viewer.capabilities as capability_module

    if capability_module.molcryskit_contract_missing():
        pytest.skip("requires the exact MolCrysKit structure-contract commit")

    from mat_viewer.agent import load_structure, prepare_render
    from mat_viewer.cube.io import read_cube

    field, density, high, low = _interaction_field()
    source = tmp_path / "interaction.cube"
    density_path = tmp_path / "density.cube"
    _write_cube(source, field)
    _write_cube(density_path, density)
    loaded = load_structure(source)
    base = RenderSpec(
        width=32,
        height=32,
        scale=1,
        backend="cpu",
        show_cell=False,
        isosurface_isovalue=0.5,
        isosurface_stride=1,
        isosurface_density_cube=str(tmp_path / "missing-density.cube"),
        isosurface_density_cutoff=0.05,
    )
    plain = prepare_render(loaded, render_spec=base)
    masked = prepare_render(
        loaded,
        render_spec=RenderSpec(
            width=32,
            height=32,
            scale=1,
            backend="cpu",
            show_cell=False,
            isosurface_isovalue=0.5,
            isosurface_stride=1,
            isosurface_density_mask=True,
            isosurface_density_cube=str(density_path),
            isosurface_density_cutoff=0.05,
        ),
    )

    def _vertices(plan) -> np.ndarray:
        rows = [
            primitive.vertices
            for primitive in plan.primitives
            if primitive.semantic_id.startswith("isosurface:")
        ]
        return np.vstack(rows)

    plain_vertices = _vertices(plain)
    masked_vertices = _vertices(masked)
    assert np.any(_closer_to(plain_vertices, high, low))
    assert np.all(_closer_to(masked_vertices, low, high))
    reread = read_cube(density_path)
    assert np.allclose(reread.values, density.values)


def _write_cube(path: Path, cube: CubeData) -> None:
    from mat_viewer.cube.io import BOHR_TO_ANGSTROM

    bohr = 1.0 / BOHR_TO_ANGSTROM
    origin = np.asarray(cube.origin, dtype=float) * bohr
    axes = np.asarray(cube.axes, dtype=float) * bohr
    shape = cube.values.shape
    values = np.asarray(cube.values, dtype=float).ravel()
    lines = [
        " ".join(f"{value:.8e}" for value in values[index : index + 6])
        for index in range(0, len(values), 6)
    ]
    path.write_text(
        "\n".join(
            [
                cube.title,
                "synthetic grid",
                f"0 {origin[0]:.8e} {origin[1]:.8e} {origin[2]:.8e}",
                f"{shape[0]} {axes[0, 0]:.8e} {axes[0, 1]:.8e} {axes[0, 2]:.8e}",
                f"{shape[1]} {axes[1, 0]:.8e} {axes[1, 1]:.8e} {axes[1, 2]:.8e}",
                f"{shape[2]} {axes[2, 0]:.8e} {axes[2, 1]:.8e} {axes[2, 2]:.8e}",
                *lines,
                "",
            ]
        ),
        encoding="utf-8",
    )
