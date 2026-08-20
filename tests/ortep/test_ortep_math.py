from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.ortep import ellipsoid_principal_axes


def test_ortep_principal_axes_are_orthonormal_and_scaled():
    U = np.diag([0.01, 0.04, 0.09])
    lengths, axes = ellipsoid_principal_axes(U)
    assert np.allclose(axes.T @ axes, np.eye(3))
    assert np.isclose(lengths[0] / lengths[-1], 3.0)


def test_ortep_probability_changes_axis_lengths():
    U = np.eye(3) * 0.04
    low, _ = ellipsoid_principal_axes(U, probability=0.5)
    high, _ = ellipsoid_principal_axes(U, probability=0.9)
    assert high[0] > low[0]
    assert np.allclose(low, low[0])


def test_ortep_rejects_invalid_u():
    with pytest.raises(ValueError):
        ellipsoid_principal_axes([[1, 2], [2, 1]])
    with pytest.raises(ValueError):
        ellipsoid_principal_axes([[1, 2, 0], [0, 1, 0], [0, 0, 1]])
    with pytest.raises(ValueError):
        ellipsoid_principal_axes(np.diag([1.0, -1.0, 1.0]))
