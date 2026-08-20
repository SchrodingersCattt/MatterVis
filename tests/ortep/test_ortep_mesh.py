from __future__ import annotations

import numpy as np

from mat_viewer.ortep import ortep_mesh3d


def test_ortep_mesh_counts_and_closed_edges():
    lat_steps = 6
    lon_steps = 10
    vertices, triangles = ortep_mesh3d([0, 0, 0], np.eye(3) * 0.04, lat_steps=lat_steps, lon_steps=lon_steps)
    assert vertices.shape == (2 + (lat_steps - 1) * lon_steps, 3)
    assert triangles.shape == (2 * lon_steps * (lat_steps - 1), 3)

    edges = {}
    for tri in triangles:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            key = tuple(sorted((int(a), int(b))))
            edges[key] = edges.get(key, 0) + 1
    assert all(count == 2 for count in edges.values())
