from __future__ import annotations

import numpy as np

from crystal_viewer.compass import camera_screen_basis


def test_camera_screen_basis_right_points_screen_right():
    camera = {
        "eye": {"x": 0.0, "y": 0.0, "z": 1.0},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 1.0, "z": 0.0},
    }

    right, screen_up = camera_screen_basis(camera)

    np.testing.assert_allclose(right, [1.0, 0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(screen_up, [0.0, 1.0, 0.0], atol=1e-12)
