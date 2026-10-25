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


@pytest.mark.parametrize("pitch", [-180, 180])
@pytest.mark.parametrize("elevation", [0, 89.99999])
def test_nearly_polar_limit_preserves_first_crossing(elevation, pitch):
    limit = 89.9999999
    camera = Camera(elevation=elevation, azimuth=0)
    result = camera.orbit_turntable(pitch_deg=pitch, max_pitch_deg=limit)
    assert np.linalg.norm(result.view_direction[:2]) == pytest.approx(
        np.cos(np.radians(limit)), rel=1e-6, abs=1e-14,
    )
    assert result.view_direction[0] > 0
    assert result.view_direction[2] == pytest.approx(np.sign(pitch))


@pytest.mark.parametrize("pitch", [-30, 30])
@pytest.mark.parametrize("roll", [0, 89.999999])
def test_small_positive_pitch_limit_is_not_ignored(roll, pitch):
    limit = 1e-11
    result = Camera(elevation=0, azimuth=0, roll=roll).orbit_turntable(
        pitch_deg=pitch, max_pitch_deg=limit,
    )
    assert result.elevation == pytest.approx(np.sign(pitch) * limit, rel=1e-6, abs=1e-14)


@pytest.mark.parametrize("pitch", [-180, 180])
@pytest.mark.parametrize("elevation,azimuth,limit", [
    (0, 0, 10), (30, 0, 60), (5, 75, 20),
    (-31.051174325430566, 41.74360572442629, 84.4734641689313),
    (-1.1673527608898961, -76.77603032718554, 1.4578832157192867),
    (0.19717342466915833, -27.50373870163881, 0.4422667228223486),
])
def test_pitch_arc_tangent_to_limit_completes(elevation, azimuth, limit, pitch):
    roll = np.degrees(np.arcsin(
        np.cos(np.radians(limit)) / np.cos(np.radians(elevation)),
    ))
    camera = Camera(elevation=elevation, azimuth=azimuth, roll=roll)
    result = camera.orbit_turntable(pitch_deg=pitch, max_pitch_deg=limit)
    assert result.view_direction == pytest.approx(-camera.view_direction)


@pytest.mark.parametrize("azimuth", [30, 90, 180, 270])
@pytest.mark.parametrize("elevation", [-90, 90])
@pytest.mark.parametrize("pitch", [-30, 30])
def test_exact_poles_escape_at_any_azimuth(azimuth, elevation, pitch):
    result = Camera(azimuth=azimuth, elevation=elevation).orbit_turntable(pitch_deg=pitch)
    assert result.elevation == pytest.approx(np.sign(elevation) * 60)


def test_repeated_outward_pitch_stays_at_rolled_boundary():
    limit = 44.294019911530256
    camera = Camera(
        azimuth=67.61836623869777,
        elevation=15.626775877604842,
        roll=24.95419986980707,
    )
    clipped = camera.orbit_turntable(pitch_deg=180, max_pitch_deg=limit)
    repeated = clipped.orbit_turntable(pitch_deg=30, max_pitch_deg=limit)
    assert repeated.view_direction == pytest.approx(clipped.view_direction)
    assert repeated.elevation == pytest.approx(limit)
