"""Regression tests for initial perspective camera framing."""

from __future__ import annotations

import numpy as np

from mat_viewer.render.viewport import _scene_ranges


def _scene(camera: dict | None = None) -> dict:
    return {
        "name": "camera-fit",
        "display_mode": "formula_unit",
        "draw_atoms": [
            {"cart": [-4.0, -0.8, 0.0], "atom_radius": 0.22, "is_minor": False},
            {"cart": [6.0, 1.2, 0.6], "atom_radius": 0.28, "is_minor": False},
            {"cart": [1.0, 0.0, -0.5], "atom_radius": 0.18, "is_minor": False},
        ],
        "camera": camera,
    }


def test_initial_camera_fit_preserves_displayed_bbox_center_and_margin() -> None:
    scene = _scene(
        {
            "position": [1.0, 1.0, 1.0],
            "focal_point": [0.0, 0.0, 0.0],
            "up": [0.0, 0.0, 1.0],
        }
    )
    legacy_ranges = _scene_ranges(
        scene,
        {"display_mode": "formula_unit", "atom_scale": 1.0},
    )
    fitted_ranges = _scene_ranges(
        scene,
        {
            "display_mode": "formula_unit",
            "atom_scale": 1.0,
            "projection": "perspective",
            "camera": scene["camera"],
        },
    )

    # Fitting may enlarge the ranges for an oblique camera, but it must not
    # move their centre away from the displayed atom bounding box.
    np.testing.assert_allclose(
        [np.mean(axis_range) for axis_range in fitted_ranges],
        [np.mean(axis_range) for axis_range in legacy_ranges],
    )
    assert all(
        (fitted[1] - fitted[0]) >= (legacy[1] - legacy[0])
        for fitted, legacy in zip(fitted_ranges, legacy_ranges)
    )
    for atom in scene["draw_atoms"]:
        for axis, axis_range in enumerate(fitted_ranges):
            assert axis_range[0] <= atom["cart"][axis] <= axis_range[1]


def test_interactive_plotly_camera_keeps_existing_viewport() -> None:
    scene = _scene(
        {
            "eye": {"x": 0.2, "y": 1.4, "z": 1.1},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        }
    )
    style = {"display_mode": "formula_unit", "atom_scale": 1.0}
    baseline = _scene_ranges(scene, style)
    interactive = _scene_ranges(scene, {**style, "camera": scene["camera"]})
    np.testing.assert_allclose(interactive, baseline)


def test_oblique_layered_structure_gets_full_perspective_margin() -> None:
    """A long Brucite-like layer must not clip the projected corner."""
    scene = {
        "name": "brucite-like",
        "display_mode": "formula_unit",
        "draw_atoms": [
            {"cart": [-8.0, -5.5, -0.2], "atom_radius": 0.7, "is_minor": False},
            {"cart": [8.0, 5.5, 0.2], "atom_radius": 0.7, "is_minor": False},
            {"cart": [0.0, 0.0, 2.0], "atom_radius": 0.4, "is_minor": False},
        ],
        "camera": {
            "position": [1.0, 1.0, 1.0],
            "focal_point": [0.0, 0.0, 0.0],
            "up": [0.0, 0.0, 1.0],
        },
    }
    ranges = _scene_ranges(
        scene,
        {"display_mode": "formula_unit", "atom_scale": 1.0, "camera": scene["camera"]},
    )
    for atom in scene["draw_atoms"]:
        assert all(axis_range[0] <= atom["cart"][axis] <= axis_range[1] for axis, axis_range in enumerate(ranges))

