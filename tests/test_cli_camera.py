from __future__ import annotations

from argparse import Namespace

import numpy as np
import pytest

from crystal_viewer.cli import _apply_camera_overrides, _plotly_static_export_available


def _args(**overrides) -> Namespace:
    values = {
        "camera_axis": None,
        "view_direction": None,
        "camera_position": None,
        "camera_up": None,
        "camera_distance": 1.8,
        "projection": "orthographic",
    }
    values.update(overrides)
    return Namespace(**values)


def test_camera_defaults_to_orthographic_positive_c_axis():
    scene = {"M": np.diag([3.0, 4.0, 5.0])}
    style = {}

    _apply_camera_overrides(scene, style, _args())

    assert np.allclose(scene["view_direction"], [0.0, 0.0, 1.0])
    assert np.allclose(scene["up"], [0.0, 1.0, 0.0])
    assert style["camera"]["eye"] == {"x": 0.0, "y": 0.0, "z": 1.8}
    assert style["camera"]["projection"] == {"type": "orthographic"}


def test_explicit_position_controls_eye_and_flat_scene_basis():
    scene = {"M": np.eye(3)}
    style = {}

    _apply_camera_overrides(
        scene,
        style,
        _args(camera_position=[2.0, 0.0, 0.0], camera_up=[0.0, 0.0, 1.0]),
    )

    assert style["camera"]["eye"] == {"x": 2.0, "y": 0.0, "z": 0.0}
    assert np.allclose(scene["view_direction"], [1.0, 0.0, 0.0])
    assert np.allclose(scene["up"], [0.0, 0.0, 1.0])


def test_zero_view_direction_is_rejected():
    with pytest.raises(ValueError, match="view direction must be non-zero"):
        _apply_camera_overrides(
            {"M": np.eye(3)},
            {},
            _args(view_direction=[0.0, 0.0, 0.0]),
        )


def test_static_export_preflight_returns_reason_when_unavailable():
    available, reason = _plotly_static_export_available()
    assert isinstance(available, bool)
    assert reason is None if available else isinstance(reason, str) and bool(reason)