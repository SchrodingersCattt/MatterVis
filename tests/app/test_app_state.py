from __future__ import annotations

from mat_viewer.app import _camera_from_relayout_data


def test_camera_from_relayout_data_accepts_dotted_partial_updates():
    current = {
        "eye": {"x": 1.0, "y": 1.0, "z": 1.0},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
    }

    camera = _camera_from_relayout_data(
        {
            "scene.camera.eye.x": 1.5,
            "scene.camera.eye.y": -0.2,
            "scene.camera.up.z": 0.8,
        },
        current,
    )

    assert camera["eye"] == {"x": 1.5, "y": -0.2, "z": 1.0}
    assert camera["center"] == current["center"]
    assert camera["up"] == {"x": 0.0, "y": 0.0, "z": 0.8}


def test_camera_from_relayout_data_ignores_non_camera_updates():
    assert _camera_from_relayout_data({"autosize": True}, None) is None
