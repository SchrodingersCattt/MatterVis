from __future__ import annotations

from pathlib import Path

import numpy as np

from mat_viewer.config.schema import BUILTIN_STYLE
from mat_viewer.cube import CubeAtom, CubeData
from mat_viewer.cube.bridge import cube_to_raw_atoms
from mat_viewer.render.traces_isosurface import (
    _cube_atom_shift,
    _periodic_component_filter,
    _periodic_component_meshes,
    _sample_isosurface_field,
)


def _periodic_cosine(shape=(9, 10, 12)) -> np.ndarray:
    u = np.arange(shape[0], dtype=float) / shape[0]
    v = np.arange(shape[1], dtype=float) / shape[1]
    w = np.arange(shape[2], dtype=float) / shape[2]
    return (
        np.cos(2 * np.pi * u[:, None, None])
        + np.cos(2 * np.pi * v[None, :, None])
        + np.cos(2 * np.pi * w[None, None, :])
    )


def test_periodic_sampling_appends_exact_wrapped_endpoints_for_odd_grid():
    values = _periodic_cosine()
    sampled, indices = _sample_isosurface_field(values, stride=2, periodic=True)

    assert sampled.shape == (6, 6, 7)
    assert np.array_equal(indices[0], [0, 2, 4, 6, 8, 9])
    assert np.array_equal(indices[1], [0, 2, 4, 6, 8, 10])
    assert np.array_equal(indices[2], [0, 2, 4, 6, 8, 10, 12])
    assert np.allclose(sampled[-1, :, :], sampled[0, :, :])
    assert np.allclose(sampled[:, -1, :], sampled[:, 0, :])
    assert np.allclose(sampled[:, :, -1], sampled[:, :, 0])


def test_nonperiodic_sampling_preserves_legacy_slice():
    values = _periodic_cosine()
    sampled, indices = _sample_isosurface_field(values, stride=2, periodic=False)

    assert sampled.shape == (5, 5, 6)
    assert np.array_equal(sampled, values[::2, ::2, ::2])
    assert np.array_equal(indices[0], [0, 2, 4, 6, 8])
    assert np.array_equal(indices[1], [0, 2, 4, 6, 8])
    assert np.array_equal(indices[2], [0, 2, 4, 6, 8, 10])


def test_periodic_mesh_reaches_all_monoclinic_cell_faces():
    from skimage.measure import marching_cubes

    shape = (9, 10, 12)
    values = _periodic_cosine(shape)
    sampled, indices = _sample_isosurface_field(values, stride=2, periodic=True)
    verts, _faces, _normals, _levels = marching_cubes(sampled, level=2.2)

    fractional_axes = [indices[i] / shape[i] for i in range(3)]
    fractional = np.column_stack([
        np.interp(verts[:, axis], np.arange(len(fractional_axes[axis])), fractional_axes[axis])
        for axis in range(3)
    ])
    assert np.all(fractional >= -1e-12)
    assert np.all(fractional <= 1 + 1e-12)
    for axis in range(3):
        assert np.any(np.isclose(fractional[:, axis], 0.0, atol=1e-7))
        assert np.any(np.isclose(fractional[:, axis], 1.0, atol=1e-7))

    lattice = np.array([[4.0, 0.0, 0.0], [0.0, 5.0, 0.0], [-1.2, 0.0, 4.8]])
    cartesian = fractional @ lattice
    assert np.allclose(cartesian, fractional @ lattice)


def test_cube_fractional_coordinates_include_nonzero_origin():
    lattice = np.array([[4.0, 0.0, 0.0], [0.0, 5.0, 0.0], [-1.2, 0.0, 4.8]])
    origin = np.array([1.2, -0.4, 0.7])
    frac = np.array([0.27, 0.41, 0.63])
    cart = origin + frac @ lattice
    cube = CubeData(
        title="origin regression",
        comment="synthetic",
        atoms=[CubeAtom(6, 6.0, cart)],
        origin=origin,
        axes=lattice / np.array([4, 5, 6])[:, None],
        values=np.zeros((4, 5, 6)),
        path=Path("origin.cube"),
    )

    raw = cube_to_raw_atoms(cube)
    assert np.allclose(raw[0]["frac"], frac)


def test_periodic_style_default_is_backward_compatible():
    assert BUILTIN_STYLE["isosurface_periodic"] is False


def test_scalar_shift_depends_only_on_cube_origin_not_draw_atom_centroid():
    cube = CubeData(
        title="shift",
        comment="synthetic",
        atoms=[CubeAtom(6, 6.0, np.array([1.0, 2.0, 3.0]))],
        origin=np.array([0.2, -0.3, 0.4]),
        axes=np.eye(3),
        values=np.zeros((2, 2, 2)),
        path=Path("shift.cube"),
    )
    scene = {
        "cube_data": cube,
        "draw_atoms": [
            {"cart": np.array([100.0, 100.0, 100.0])},
            {"cart": np.array([-50.0, 20.0, 30.0])},
        ],
    }
    assert np.allclose(_cube_atom_shift(scene), -cube.origin)


def test_periodic_component_filter_merges_opposite_faces():
    mask = np.zeros((5, 4, 4), dtype=bool)
    mask[0, 1, 1] = True
    mask[-1, 1, 1] = True

    assert not _periodic_component_filter(mask, minimum=2, periodic=False).any()
    assert _periodic_component_filter(mask, minimum=2, periodic=True).sum() == 2


def test_face_crossing_periodic_gaussian_has_one_watertight_representative():
    shape = (24, 25, 26)
    u = np.arange(shape[0]) / shape[0]
    v = np.arange(shape[1]) / shape[1]
    w = np.arange(shape[2]) / shape[2]
    du = np.minimum(u, 1.0 - u)[:, None, None]
    dv = (v - 0.5)[None, :, None]
    dw = (w - 0.5)[None, None, :]
    values = np.exp(-0.5 * ((du / 0.16) ** 2 + (dv / 0.14) ** 2 + (dw / 0.14) ** 2))
    sampled, indices = _sample_isosurface_field(values, stride=1, periodic=False)

    meshes = _periodic_component_meshes(
        sampled,
        indices,
        full_shape=shape,
        level=0.35,
        minimum_voxels=0,
    )
    assert len(meshes) == 1
    vertices, faces = meshes[0]
    assert vertices[:, 0].min() < 0 < vertices[:, 0].max()
    edges = np.sort(
        np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    _unique, counts = np.unique(edges, axis=0, return_counts=True)
    assert np.all(counts == 2)


def test_nearest_atom_policy_selects_an_integer_lattice_image():
    shape = (16, 12, 12)
    u = np.arange(shape[0]) / shape[0]
    v = np.arange(shape[1]) / shape[1]
    w = np.arange(shape[2]) / shape[2]
    du = np.minimum(u, 1.0 - u)[:, None, None]
    values = np.exp(-0.5 * ((du / 0.16) ** 2 + ((v - 0.5)[None, :, None] / 0.14) ** 2 + ((w - 0.5)[None, None, :] / 0.14) ** 2))
    sampled, indices = _sample_isosurface_field(values, stride=1, periodic=False)

    base_mesh = _periodic_component_meshes(
        sampled, indices, full_shape=shape, level=0.35, minimum_voxels=0,
    )[0][0]
    target = np.array([[shape[0] * 1.05, shape[1] * 0.5, shape[2] * 0.5]])
    shifted_mesh = _periodic_component_meshes(
        sampled,
        indices,
        full_shape=shape,
        level=0.35,
        minimum_voxels=0,
        target_indices=target,
    )[0][0]
    shift = shifted_mesh.mean(axis=0) - base_mesh.mean(axis=0)
    assert np.allclose(shift, [shape[0], 0.0, 0.0])


def test_build_cube_figure_periodic_argument_and_style_precedence(tmp_path, monkeypatch):
    from mat_viewer.cube import build_cube_figure
    from mat_viewer.loader import cube_adapter
    from mat_viewer.render import figures

    cube_path = tmp_path / "minimal.cube"
    cube_path.write_text(
        "periodic wrapper\nsynthetic\n"
        "    0 0.0 0.0 0.0\n"
        "    2 0.5 0.0 0.0\n"
        "    2 0.0 0.5 0.0\n"
        "    2 0.0 0.0 0.5\n"
        " 0 0 0 0 0 0 0 0\n"
    )
    cube = CubeData(
        title="periodic wrapper",
        comment="synthetic",
        atoms=[],
        origin=np.zeros(3),
        axes=np.eye(3) * 0.5,
        values=np.zeros((2, 2, 2)),
        path=cube_path,
    )
    bundle = type("Bundle", (), {"cube_data": cube})()
    monkeypatch.setattr(cube_adapter, "load_cube_file", lambda *_args, **_kwargs: bundle)
    monkeypatch.setattr(
        "mat_viewer.loader.core.build_bundle_scene",
        lambda *_args, **_kwargs: {"cube_data": cube},
    )
    captured = {}

    def fake_build_figure(_scene, style):
        captured.update(style)
        return object()

    monkeypatch.setattr(figures, "build_figure", fake_build_figure)

    build_cube_figure(cube_path, periodic=True)
    assert captured["isosurface_periodic"] is True
    captured.clear()
    build_cube_figure(cube_path, periodic=True, style={"isosurface_periodic": False})
    assert captured["isosurface_periodic"] is False
