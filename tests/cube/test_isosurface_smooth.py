"""Taubin smoothing. The historical mesh default stays stride 2 and unsmoothed."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.cli import _build_render_parser
from mat_viewer.config.schema import BUILTIN_STYLE
from mat_viewer.cube.color_field import colormap_rgb, isosurface_preparation_token
from mat_viewer.cube.cpu import _contain_smoothed_vertices, cube_isosurface_meshes
from mat_viewer.cube.io import CubeData
from mat_viewer.cube.smooth import (
    TAUBIN_ITERATIONS,
    TAUBIN_LAMBDA,
    TAUBIN_MU,
    area_weighted_vertex_normals,
    boundary_vertex_mask,
    scalar_gradient_normals,
    taubin_smooth,
)
from mat_viewer.render.cli_controls import isosurface_render_fields
from mat_viewer.render.contracts import RenderSpec


def _sphere_cube(tmp_path: Path) -> CubeData:
    coordinates = np.indices((16, 16, 16), dtype=float)
    radius = np.linalg.norm(coordinates - 7.5, axis=0)
    return CubeData(
        title="sphere",
        comment="",
        atoms=[],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.3,
        values=np.exp(-0.5 * (radius / 3.5) ** 2),
        path=tmp_path / "sphere.cube",
    )


def _lobes_cube(tmp_path: Path) -> CubeData:
    coordinates = np.indices((18, 18, 22), dtype=float)
    center = np.array([8.5, 8.5, 10.5])
    offset = center.reshape(3, 1, 1, 1)
    positive = np.exp(-np.sum((coordinates - offset - np.array([0, 0, 4]).reshape(3, 1, 1, 1)) ** 2, axis=0) / 6.0)
    negative = -np.exp(-np.sum((coordinates - offset + np.array([0, 0, 4]).reshape(3, 1, 1, 1)) ** 2, axis=0) / 6.0)
    return CubeData(
        title="lobes",
        comment="",
        atoms=[],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.4,
        values=positive + negative,
        path=tmp_path / "lobes.cube",
    )


def _mean_umbrella(vertices: np.ndarray, triangles: np.ndarray) -> float:
    faces = np.asarray(triangles, dtype=np.int64)
    pairs = np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    edges = np.vstack((pairs, pairs[:, ::-1]))
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges = edges[order]
    keep = np.empty(len(edges), dtype=bool)
    keep[0] = True
    keep[1:] = np.any(edges[1:] != edges[:-1], axis=1)
    edges = edges[keep]
    counts = np.bincount(edges[:, 0], minlength=len(vertices))
    summed = np.zeros_like(vertices)
    np.add.at(summed, edges[:, 0], vertices[edges[:, 1]])
    degree = np.maximum(counts.astype(float), 1.0)
    delta = summed / degree[:, None] - vertices
    delta[counts == 0] = 0.0
    return float(np.mean(np.linalg.norm(delta, axis=1)))


def test_taubin_defaults_and_sphere_is_smoother_without_a_topology_change(tmp_path: Path) -> None:
    cube = _sphere_cube(tmp_path)
    raw = cube_isosurface_meshes(cube, isovalue=0.45, smooth=False)
    smooth = cube_isosurface_meshes(cube, isovalue=0.45, smooth=True)
    assert TAUBIN_LAMBDA == pytest.approx(0.5)
    assert TAUBIN_MU == pytest.approx(-0.53)
    assert 10 <= TAUBIN_ITERATIONS <= 20
    assert len(raw) == 1 and len(smooth) == 1
    assert np.array_equal(raw[0]["triangles"], smooth[0]["triangles"])
    assert raw[0]["vertices"].shape == smooth[0]["vertices"].shape
    assert _mean_umbrella(smooth[0]["vertices"], smooth[0]["triangles"]) < (
        _mean_umbrella(raw[0]["vertices"], raw[0]["triangles"]) * 0.55
    )
    center = smooth[0]["vertices"].mean(axis=0)
    normals = smooth[0]["normals"]
    assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5)
    outward = np.sum(normals * (smooth[0]["vertices"] - center), axis=1)
    assert float(np.mean(outward)) > 0.0
    assert not np.allclose(raw[0]["vertices"], smooth[0]["vertices"])
    assert np.allclose(
        raw[0]["vertices"],
        cube_isosurface_meshes(cube, isovalue=0.45, smooth=False)[0]["vertices"],
    )


def test_phases_stay_separate_and_boundary_vertices_are_pinned(tmp_path: Path) -> None:
    lobes = cube_isosurface_meshes(_lobes_cube(tmp_path), isovalue=0.25, smooth=True)
    assert {mesh["id"] for mesh in lobes} == {"cube:positive", "cube:negative"}
    positive = next(mesh for mesh in lobes if mesh["id"] == "cube:positive")
    negative = next(mesh for mesh in lobes if mesh["id"] == "cube:negative")
    assert float(positive["vertices"][:, 2].min()) > float(negative["vertices"][:, 2].max())

    pinned = np.array([[0.0, 1.0, 1.0], [1.0, 1.0, 1.0], [2.0, 1.0, 1.0]])
    mask = boundary_vertex_mask(pinned, (5, 5, 5))
    assert mask.tolist() == [True, False, False]
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    moved = taubin_smooth(pinned, faces, pinned=mask, iterations=8)
    assert np.allclose(moved[0], pinned[0])
    assert not np.allclose(moved[1], pinned[1])


def test_historical_stride_and_smoothing_stay_opt_in(tmp_path: Path) -> None:
    spec = RenderSpec()
    assert spec.isosurface_stride == 2
    assert spec.isosurface_smooth is False
    assert BUILTIN_STYLE["isosurface_stride"] == 2
    assert BUILTIN_STYLE["isosurface_smooth"] is False
    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    args = render.parse_args(["density.cube", "-o", "density.png"])
    fields = isosurface_render_fields(args)
    assert fields["isosurface_stride"] == 2
    assert fields["isosurface_smooth"] is False
    publication = render.parse_args(
        [
            "density.cube",
            "-o",
            "density.png",
            "--isosurface-stride",
            "1",
            "--isosurface-smooth",
        ]
    )
    publication_fields = isosurface_render_fields(publication)
    assert publication_fields["isosurface_stride"] == 1
    assert publication_fields["isosurface_smooth"] is True
    cube = _sphere_cube(tmp_path)
    # The mesh helper's own stride default stays 1. Smoothing stays off
    # until the caller opts in, so the default vertices are the raw mesh.
    raw = cube_isosurface_meshes(cube, isovalue=0.45)
    assert raw[0]["vertices"].shape == cube_isosurface_meshes(
        cube, isovalue=0.45, stride=1, smooth=False
    )[0]["vertices"].shape
    smooth = cube_isosurface_meshes(cube, isovalue=0.45, stride=1, smooth=True)
    coarse = cube_isosurface_meshes(cube, isovalue=0.45, stride=2, smooth=False)
    assert len(coarse[0]["vertices"]) < len(raw[0]["vertices"])
    assert not np.allclose(raw[0]["vertices"], smooth[0]["vertices"])


def _index_color_cube(cube: CubeData) -> CubeData:
    coordinates = np.indices(cube.values.shape, dtype=float)
    return CubeData(
        title=f"{cube.title}-color",
        comment="",
        atoms=[],
        origin=np.array(cube.origin, dtype=float, copy=True),
        axes=np.array(cube.axes, dtype=float, copy=True),
        values=coordinates[0].copy(),
        path=cube.path.with_name(f"{cube.path.stem}-color.cube"),
    )


def test_color_cube_is_sampled_at_post_taubin_vertices(tmp_path: Path) -> None:
    cube = _sphere_cube(tmp_path)
    color = _index_color_cube(cube)
    span = (0.0, float(cube.values.shape[0] - 1))
    raw = cube_isosurface_meshes(
        cube, isovalue=0.45, smooth=False, color_cube=color, color_range=span
    )
    smooth = cube_isosurface_meshes(
        cube, isovalue=0.45, smooth=True, color_cube=color, color_range=span
    )
    assert not np.allclose(raw[0]["vertices"], smooth[0]["vertices"])
    drawn_index = smooth[0]["vertices"] @ np.linalg.inv(cube.axes)
    expected = colormap_rgb("bgr", drawn_index[:, 0], span[0], span[1])
    raw_index = raw[0]["vertices"] @ np.linalg.inv(cube.axes)
    at_raw_positions = colormap_rgb("bgr", raw_index[:, 0], span[0], span[1])
    assert np.allclose(smooth[0]["vertex_colors"], expected, atol=1e-5)
    assert not np.allclose(smooth[0]["vertex_colors"], at_raw_positions, atol=1e-3)


def test_mu_overshoot_is_pulled_back_onto_the_data_box() -> None:
    basis = np.eye(3)
    world = np.asarray([[-0.25, 1.0, 2.0], [1.5, 1.0, 2.0], [19.4, 1.0, 2.0]])
    contained = _contain_smoothed_vertices(world, basis, (8, 8, 8))
    assert contained[0, 0] == pytest.approx(0.0)
    assert contained[2, 0] == pytest.approx(7.0)
    assert np.allclose(contained[1], world[1])


def test_near_face_shell_color_sample_stays_inside_the_grid(tmp_path: Path) -> None:
    coordinates = np.indices((24, 16, 16), dtype=float)
    field = np.exp(-0.5 * ((coordinates[0] - 0.4) / 0.25) ** 2)
    axes = np.eye(3) * 0.2
    cube = CubeData(
        title="shell",
        comment="",
        atoms=[],
        origin=np.zeros(3),
        axes=axes,
        values=field,
        path=tmp_path / "shell.cube",
    )
    color = _index_color_cube(cube)
    span = (0.0, float(field.shape[0] - 1))
    meshes = cube_isosurface_meshes(
        cube, isovalue=0.2, smooth=True, color_cube=color, color_range=span
    )
    index = meshes[0]["vertices"] @ np.linalg.inv(axes)
    upper = np.asarray(field.shape, dtype=float) - 1.0
    assert float(index.min()) >= -1.0e-6
    assert float((index - upper).max()) <= 1.0e-6
    expected = colormap_rgb("bgr", index[:, 0], span[0], span[1])
    assert np.allclose(meshes[0]["vertex_colors"], expected, atol=1e-5)


def test_preparation_token_defaults_to_the_raw_mesh(tmp_path: Path) -> None:
    cube = _sphere_cube(tmp_path)
    common = {
        "isovalue": 0.45,
        "opacity": 0.55,
        "positive_color": "#D55E00",
        "negative_color": "#0072B2",
        "stride": 1,
        "ambient": 0.68,
        "diffuse": 0.32,
        "color_cube": None,
        "color_range": None,
        "color_center": None,
        "colormap": "bgr",
    }
    implied = isosurface_preparation_token(cube, **common)
    explicit = isosurface_preparation_token(
        cube,
        **common,
        smooth=True,
        smooth_iterations=TAUBIN_ITERATIONS,
    )
    raw = isosurface_preparation_token(
        cube, **common, smooth=False, smooth_iterations=0
    )
    assert implied == raw
    assert implied != explicit


def test_scalar_gradient_normals_follow_the_field_and_fall_back() -> None:
    index = np.indices((6, 5, 4), dtype=float)
    values = 0.2 * index[0] - 0.1 * index[2]
    samples = np.array([[2.2, 2.0, 1.5], [-2.0, 1.0, 1.0]])
    fallback = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0]])
    normals = scalar_gradient_normals(
        values,
        samples,
        world_from_index=np.eye(3),
        fallback=fallback,
    )
    gradient = np.array([0.2, 0.0, -0.1])
    gradient /= np.linalg.norm(gradient)
    if float(np.dot(gradient, fallback[0])) < 0.0:
        gradient = -gradient
    assert np.allclose(normals[0], gradient, atol=1e-5)
    assert np.allclose(normals[1], fallback[1], atol=1e-8)
    assert area_weighted_vertex_normals(np.zeros((0, 3)), np.zeros((0, 3))).shape == (0, 3)


def test_smoothed_sphere_normals_track_the_radial_field(tmp_path: Path) -> None:
    mesh = cube_isosurface_meshes(_sphere_cube(tmp_path), isovalue=0.45, smooth=True)[0]
    center = mesh["vertices"].mean(axis=0)
    radial = mesh["vertices"] - center
    radial /= np.linalg.norm(radial, axis=1, keepdims=True)
    cosine = np.sum(mesh["normals"] * radial, axis=1)
    assert float(np.mean(cosine)) > 0.98
    assert float(np.percentile(cosine, 5)) > 0.95
