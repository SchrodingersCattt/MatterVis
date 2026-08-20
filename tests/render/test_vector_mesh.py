from __future__ import annotations

import numpy as np
import pytest

from crystal_viewer.render.meshes import arrow_mesh_geometry


def test_arrow_mesh_has_exact_origin_tip_and_no_degenerate_faces() -> None:
    origin = np.array([1.0, -2.0, 0.5])
    end = np.array([2.0, 1.0, 4.5])
    vertices, faces = arrow_mesh_geometry(origin, end, shaft_radius=0.12, sides=12)
    assert np.allclose(vertices[-2], origin)
    assert np.allclose(vertices[-1], end)
    triangles = vertices[faces]
    areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2.0
    assert np.all(areas > 1.0e-10)
    assert len(vertices) == 38
    assert len(faces) == 72


@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": [0, 0, 0], "end": [0, 0, 0]},
        {"origin": [0, 0, 0], "end": [1, 0, 0], "shaft_radius": 0},
        {"origin": [0, 0, 0], "end": [1, 0, 0], "head_length_ratio": 1},
        {"origin": [0, 0, 0], "end": [1, 0, 0], "sides": 2},
    ],
)
def test_arrow_mesh_rejects_invalid_geometry(kwargs) -> None:
    with pytest.raises(ValueError):
        arrow_mesh_geometry(**kwargs)
