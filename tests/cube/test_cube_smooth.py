"""Trilinear gradient sampling and isosurface normal orientation."""

from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.cube.smooth import _sample_trilinear, scalar_gradient_normals


def _trilinear_polynomial(points: np.ndarray) -> np.ndarray:
    x, y, z = points[:, 0], points[:, 1], points[:, 2]
    return (
        1.0
        + 2.0 * x
        + 3.0 * y
        + 4.0 * z
        + 5.0 * x * y
        + 6.0 * x * z
        + 7.0 * y * z
        + 8.0 * x * y * z
    )


def test_sample_trilinear_matches_a_multilinear_field() -> None:
    coordinates = np.indices((4, 4, 4), dtype=float)
    scalar = _trilinear_polynomial(coordinates.reshape(3, -1).T).reshape(4, 4, 4)
    vector = np.stack((scalar, 2.0 * scalar, scalar + coordinates[1]), axis=-1)
    points = np.array(
        [
            [1.2, 1.25, 1.5],
            [0.0, 2.0, 3.0],
            [2.4, 0.3, 1.7],
        ]
    )
    sampled_scalar, inside = _sample_trilinear(scalar, points)
    sampled_vector, vector_inside = _sample_trilinear(vector, points)
    assert np.all(inside) and np.all(vector_inside)
    expected = _trilinear_polynomial(points)
    y = points[:, 1]
    assert sampled_scalar == pytest.approx(expected)
    assert sampled_vector[:, 0] == pytest.approx(expected)
    assert sampled_vector[:, 1] == pytest.approx(2.0 * expected)
    assert sampled_vector[:, 2] == pytest.approx(expected + y)

    y_field = coordinates[1]
    y_sample, _ = _sample_trilinear(y_field, points[:1])
    assert y_sample[0] == pytest.approx(1.25)


def test_sample_trilinear_is_continuous_across_integer_planes() -> None:
    coordinates = np.indices((6, 6, 6), dtype=float)
    scalar = _trilinear_polynomial(coordinates.reshape(3, -1).T).reshape(6, 6, 6)
    vector = np.stack((scalar, coordinates[1], coordinates[2]), axis=-1)
    base = np.array([1.2, 1.25, 1.5])
    for axis in range(3):
        below = base.copy()
        above = base.copy()
        below[axis] = 2.0 - 1.0e-6
        above[axis] = 2.0 + 1.0e-6
        low, _ = _sample_trilinear(vector, below[None, :])
        high, _ = _sample_trilinear(vector, above[None, :])
        assert low[0] == pytest.approx(high[0], abs=1.0e-4)

    y_field = coordinates[1]
    before, _ = _sample_trilinear(y_field, np.array([[1.2, 1.25, 2.0 - 1.0e-6]]))
    after, _ = _sample_trilinear(y_field, np.array([[1.2, 1.25, 2.0 + 1.0e-6]]))
    assert before[0] == pytest.approx(1.25, abs=1.0e-5)
    assert after[0] == pytest.approx(1.25, abs=1.0e-5)
    assert before[0] == pytest.approx(after[0], abs=1.0e-5)


def test_scalar_gradient_normals_match_a_quadratic_radius() -> None:
    shape = (8, 8, 8)
    center = np.array([3.2, 2.7, 3.4])
    coordinates = np.indices(shape, dtype=float)
    offset = coordinates - center.reshape(3, 1, 1, 1)
    values = np.sum(offset ** 2, axis=0)
    samples = np.array(
        [
            [1.2, 1.25, 1.5],
            [4.4, 2.1, 2.0 - 1.0e-6],
            [4.4, 2.1, 2.0 + 1.0e-6],
            [5.5, 3.3, 4.8],
        ]
    )
    radial = samples - center
    exact = radial / np.linalg.norm(radial, axis=1, keepdims=True)
    normals = scalar_gradient_normals(
        values,
        samples,
        world_from_index=np.eye(3),
        fallback=exact,
    )
    assert normals == pytest.approx(exact, abs=1.0e-6)
    assert normals[1] == pytest.approx(normals[2], abs=1.0e-5)


def test_gradient_sign_vote_ignores_one_steep_outlier() -> None:
    coordinates = np.indices((12, 6, 6), dtype=float)
    values = coordinates[0] ** 3
    moderate = np.array([[1.4, 2.2, 2.5]] * 6)
    steep = np.array([[9.3, 2.2, 2.5]])
    samples = np.vstack((moderate, steep))
    direction = np.array([1.0, 0.0, 0.0])
    fallback = np.tile(direction, (len(samples), 1))
    fallback[-1] *= -1.0
    normals = scalar_gradient_normals(
        values,
        samples,
        world_from_index=np.eye(3),
        fallback=fallback,
    )
    assert np.all(normals[:, 0] > 0.9)
    assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1.0e-6)
