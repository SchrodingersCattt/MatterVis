from __future__ import annotations

import math

import numpy as np

from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.presets import DEFAULT_STYLE
from crystal_viewer.renderer import _axis_triad_segments, _scene_ranges, build_figure


def _empty_scene():
    return {
        "name": "test",
        "title": "Test",
        "M": np.eye(3),
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
        "draw_atoms": [],
        "bonds": [],
        "label_items": [],
    }


def test_build_figure_honors_orthographic_projection_and_eye_distance():
    scene = _empty_scene()
    style = {
        **DEFAULT_STYLE,
        "projection": "orthographic",
        "camera_eye_distance": 3.0,
        "show_title": False,
        "show_axes": False,
    }

    fig = build_figure(scene, style)
    camera = fig.layout.scene.camera

    assert camera.projection.type == "orthographic"
    eye_norm = math.sqrt(camera.eye.x**2 + camera.eye.y**2 + camera.eye.z**2)
    assert math.isclose(eye_norm, 3.0, rel_tol=1e-9)


def test_axis_triad_is_inside_scene_ranges_for_sy():
    bundle = build_loaded_crystal(
        name="SY", cif_path="scripts/data/SY.cif", title="SY"
    )
    style = {**DEFAULT_STYLE, **bundle.scene.get("style", {}), "show_axes": True}

    ranges = _scene_ranges(bundle.scene, style)
    _, label_positions = _axis_triad_segments(bundle.scene, style)

    assert [label for _, label in label_positions] == ["a", "b", "c"]
    for point, label in label_positions:
        assert ranges[0][0] <= point[0] <= ranges[0][1], f"{label} x clipped"
        assert ranges[1][0] <= point[1] <= ranges[1][1], f"{label} y clipped"
        assert ranges[2][0] <= point[2] <= ranges[2][1], f"{label} z clipped"
