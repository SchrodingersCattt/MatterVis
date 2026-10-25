"""Regression coverage for turntable escape from exact/near polar views."""
import numpy as np
import pytest

from mat_viewer.math.camera import Camera
from mat_viewer.math.rotation import orthonormal_camera_basis


@pytest.mark.parametrize("elevation,pitch,expected", [
    (90, -30, 60), (90, 30, 60), (-90, -30, -60), (-90, 30, -60),
    (60, 40, 89), (60, -30, 30), (89.5, -0.25, 89.25),
    (89.5, 30, 89.5), (0, 180, 89), (0, 1e12, 89),
    (90, -180, -89), (90, 180, -89),
])
def test_polar_escape_and_first_boundary(elevation, pitch, expected):
    camera = Camera(azimuth=0, elevation=elevation)
    # Use a world-up basis for nonpolar cases; preserve the public Euler
    # basis at the exact poles, whose horizontal forward direction is free.
    if abs(elevation) < 90:
        camera = Camera(basis=orthonormal_camera_basis(camera.view_direction, np.array([0., 0., 1.])))
    result = camera.orbit_turntable(pitch_deg=pitch)
    assert result.elevation == pytest.approx(expected, abs=1e-7)
    assert result.rotation_matrix @ result.rotation_matrix.T == pytest.approx(np.eye(3))
    assert np.linalg.det(result.rotation_matrix) == pytest.approx(1)


def test_pole_directions_are_opposite():
    camera = Camera(elevation=90, azimuth=0)
    positive = camera.orbit_turntable(pitch_deg=30)
    negative = camera.orbit_turntable(pitch_deg=-30)
    assert positive.view_direction[:2] == pytest.approx(-negative.view_direction[:2])
    assert positive.view_direction[2] == pytest.approx(negative.view_direction[2])


def test_narrow_cap_cannot_be_skipped_by_large_request():
    camera = Camera(elevation=0, azimuth=0)
    result = camera.orbit_turntable(pitch_deg=177, max_pitch_deg=89.99)
    assert result.elevation == pytest.approx(89.99, abs=1e-7)
