from __future__ import annotations

import numpy as np
import pytest

from crystal_viewer.math.camera import Camera, project_points
from crystal_viewer.math.rotation import axis_camera_basis, rotate_vector


def test_turntable_yaw_uses_world_positive_z() -> None:
    camera = Camera(azimuth=0.0, elevation=0.0)

    rotated = camera.orbit_turntable(yaw_deg=90.0)

    assert rotated.view_direction == pytest.approx(np.array([0.0, 1.0, 0.0]))
    assert np.allclose(rotated.rotation_matrix @ rotated.rotation_matrix.T, np.eye(3))


def test_first_turntable_orbit_preserves_existing_render_basis() -> None:
    camera = Camera(azimuth=30.0, elevation=20.0, roll=35.0)
    before = camera.rotation_matrix
    expected_forward = rotate_vector(before[2], np.array([0.0, 0.0, 1.0]), 10.0)
    expected_up = rotate_vector(before[1], np.array([0.0, 0.0, 1.0]), 10.0)

    rotated = camera.orbit_turntable(yaw_deg=10.0)

    assert rotated.view_direction == pytest.approx(expected_forward)
    assert rotated.rotation_matrix[1] == pytest.approx(expected_up)


def test_turntable_pitch_uses_current_screen_right_axis() -> None:
    camera = Camera(azimuth=30.0, elevation=20.0, roll=35.0).orbit_turntable()
    before = camera.rotation_matrix
    expected_forward = rotate_vector(before[2], -before[0], 15.0)

    rotated = camera.orbit_turntable(pitch_deg=15.0)

    assert rotated.view_direction == pytest.approx(expected_forward)
    assert rotated.target == pytest.approx(camera.target)
    assert rotated.viewport_zoom == pytest.approx(camera.viewport_zoom)


def test_turntable_orbit_retains_continuous_basis_after_roll() -> None:
    camera = Camera(azimuth=0.0, elevation=0.0).orbit_turntable(roll_deg=90.0)
    expected_forward = rotate_vector(camera.view_direction, -camera.rotation_matrix[0], 10.0)

    rotated = camera.orbit_turntable(pitch_deg=10.0)

    assert rotated.view_direction == pytest.approx(expected_forward)


def test_turntable_pitch_is_clamped_at_poles() -> None:
    rotated = Camera(azimuth=0.0, elevation=0.0).orbit_turntable(pitch_deg=180.0)

    assert rotated.elevation == pytest.approx(89.0)
    assert np.isfinite(rotated.rotation_matrix).all()
    assert np.allclose(rotated.rotation_matrix @ rotated.rotation_matrix.T, np.eye(3))


def test_turntable_huge_pitch_has_bounded_work_and_first_pole_result() -> None:
    rotated = Camera(azimuth=0.0, elevation=0.0).orbit_turntable(pitch_deg=1e12)

    assert rotated.elevation == pytest.approx(89.0)


def test_legacy_rotate_remains_euler_based() -> None:
    camera = Camera(azimuth=5.0, elevation=10.0, roll=15.0).orbit_turntable(yaw_deg=20.0)

    rotated = camera.rotate(d_azim=10.0, d_elev=-5.0, d_roll=5.0)

    assert rotated.basis is None
    assert rotated.azimuth == pytest.approx((camera.azimuth + 10.0) % 360.0)
    assert rotated.elevation == pytest.approx(camera.elevation - 5.0)
    assert rotated.roll == pytest.approx((camera.roll + 5.0) % 360.0)


def test_lattice_axis_alignment_uses_real_and_reciprocal_vectors() -> None:
    matrix = np.array([
        [3.0, 0.0, 0.0],
        [1.0, 2.0, 0.0],
        [0.5, 0.25, 4.0],
    ])
    camera = Camera().align_lattice_axis(matrix, "b")
    reciprocal = Camera().align_lattice_axis(matrix, "b*")

    assert camera.rotation_matrix == pytest.approx(axis_camera_basis(matrix, "b"))
    assert reciprocal.rotation_matrix == pytest.approx(axis_camera_basis(matrix, "b*"))
    assert not np.allclose(camera.view_direction, reciprocal.view_direction)


def test_projected_depth_convention_is_preserved_after_turntable_orbit() -> None:
    camera = Camera(azimuth=0.0, elevation=0.0).orbit_turntable(yaw_deg=45.0)
    forward = camera.view_direction
    points = np.array([np.zeros(3), forward])

    _, depth = project_points(camera, points)

    assert depth[1] > depth[0]


def test_perspective_projection_makes_nearer_points_larger() -> None:
    camera = Camera(azimuth=0.0, elevation=0.0, distance=10.0, perspective_near_is_larger=True)
    camera.projection = camera.projection.PERSPECTIVE
    points = np.array([
        [0.0, 1.0, 1.0],
        [5.0, 1.0, 1.0],
    ])

    xy, depth = project_points(camera, points)

    assert depth[1] > depth[0]
    assert abs(xy[1, 0]) > abs(xy[0, 0])