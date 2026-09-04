from __future__ import annotations

import math

import numpy as np
import pytest

from mat_viewer.loader import build_empty_bundle
from mat_viewer.math import implicit_surface_mesh
from mat_viewer.math.implicit import _marching_tetrahedra
from mat_viewer.presets import DEFAULT_STYLE
from mat_viewer.render.figures import build_figure
from mat_viewer.renderer import implicit_entity


def _scene() -> dict:
    scene = build_empty_bundle().scene
    scene["display_mode"] = "cluster"
    scene["draw_atoms"] = []
    scene["bonds"] = []
    return scene


def test_vectorised_sphere_implicit_mesh_is_a_real_surface():
    vertices, faces = implicit_surface_mesh(
        lambda points: np.sum(points**2, axis=1) - 4.0,
        ((-3.0, 3.0), (-3.0, 3.0), (-3.0, 3.0)),
        resolution=20,
    )

    assert vertices.ndim == 2 and vertices.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 3
    assert len(vertices) > 100 and len(faces) > 100
    radii = np.linalg.norm(vertices, axis=1)
    assert np.allclose(radii, 2.0, atol=0.04)
    assert np.all(vertices >= -3.0 - 1e-12)
    assert np.all(vertices <= 3.0 + 1e-12)


def test_three_array_and_scalar_field_signatures_are_supported():
    vertices, faces = implicit_surface_mesh(
        lambda x, y, z: x + 2.0 * y - z,
        ((-1.0, 1.0), (-1.0, 1.0), (-1.0, 1.0)),
        resolution=(9, 10, 11),
    )
    assert len(vertices) > 0 and len(faces) > 0

    scalar_vertices, scalar_faces = implicit_surface_mesh(
        lambda x, y, z: math.sqrt(x * x + y * y + z * z) - 0.75,
        ((-1.0, 1.0),) * 3,
        resolution=9,
    )
    assert len(scalar_vertices) > 0 and len(scalar_faces) > 0


def test_implicit_entity_routes_to_mesh3d_and_keeps_only_serialisable_metadata():
    scene = _scene()
    scene["geometry_entities"] = [
        implicit_entity(
            lambda points: np.sum(points**2, axis=1) - 1.0,
            (axis for axis in ((-1.5, 1.5),) * 3),
            resolution=(12, 13, 14),
            name="sphere",
            entity_id="sphere-1",
            color="#3399CC",
        )
    ]
    style = {
        **DEFAULT_STYLE,
        "material": "mesh",
        "style": "ball",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": False,
    }
    figure = build_figure(scene, style)
    meshes = [trace for trace in figure.data if trace.name == "sphere"]
    assert len(meshes) == 1
    assert meshes[0].type == "mesh3d"
    assert meshes[0].meta["implicit"] is True
    assert meshes[0].meta["resolution"] == [12, 13, 14]
    assert "field" not in scene["geometry_entities"][0]


def test_dependency_free_fallback_handles_a_plane():
    bounds = np.asarray(((-1.0, 1.0),) * 3, dtype=float)
    axes = [np.linspace(low, high, 8) for low, high in bounds]
    grid = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1)
    values = grid[..., 2] - 0.125
    vertices, faces = _marching_tetrahedra(values, bounds, 0.0)
    assert len(vertices) > 0 and len(faces) > 0
    assert np.allclose(vertices[:, 2], 0.125, atol=1e-12)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"bounds": ((0.0, 0.0),) * 3}, "bounds"),
        ({"bounds": ((-1.0, 1.0),) * 3, "resolution": 1}, "resolution"),
        ({"bounds": ((-1.0, 1.0),) * 3, "level": float("nan")}, "level"),
    ],
)
def test_implicit_surface_input_errors_are_contextual(kwargs, message):
    with pytest.raises(ValueError, match=message):
        implicit_surface_mesh(lambda points: np.zeros(len(points)), **kwargs)


def test_no_crossing_level_is_reported():
    with pytest.raises(ValueError, match="does not cross level"):
        implicit_surface_mesh(
            lambda points: np.ones(len(points)),
            ((-1.0, 1.0),) * 3,
            resolution=8,
        )


def test_implicit_entity_rejects_fractional_resolution():
    with pytest.raises(ValueError, match="resolution"):
        implicit_entity(
            lambda points: np.sum(points**2, axis=1) - 1.0,
            ((-2.0, 2.0),) * 3,
            resolution=2.5,
            name="invalid",
        )
