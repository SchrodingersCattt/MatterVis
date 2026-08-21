from __future__ import annotations

import numpy as np

from mat_viewer.ortep import ortep_billboard_polygon


def test_ortep_billboard_matches_legacy_projection_scale_for_default_probability():
    U = np.diag([0.04, 0.01, 0.09])
    verts, a_ax, b_ax = ortep_billboard_polygon(
        [0, 0, 0],
        U,
        [1, 0, 0],
        [0, 1, 0],
        probability=0.5,
        n_pts=8,
    )
    expected = np.sqrt(1.3862943611198906 * np.array([0.01, 0.04]))
    assert np.allclose(sorted([a_ax, b_ax]), sorted(expected))
    assert verts.shape == (8, 3)
