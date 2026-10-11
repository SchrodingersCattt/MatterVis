"""Contract tests for the shared rendering viewport resolver."""

from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render.viewport import (
    ViewportSpec,
    _axis_cube_scale,
    figure_axis_layout,
    resolve_viewport,
)


def _scene() -> dict:
    return {
        "name": "resolver",
        "draw_atoms": [
            {"cart": [0.0, 0.0, 0.0], "atom_radius": 0.2},
            {"cart": [2.0, 4.0, 1.0], "atom_radius": 0.2},
        ],
    }


def test_resolve_viewport_returns_one_immutable_scale_contract() -> None:
    scene = _scene()
    style = {"display_mode": "formula_unit", "atom_scale": 1.0}

    viewport = resolve_viewport(scene, style)

    assert isinstance(viewport, ViewportSpec)
    assert viewport.ranges == viewport.axis_ranges
    assert viewport.aspectmode == "manual"
    spans = np.asarray(
        [axis_range[1] - axis_range[0] for axis_range in viewport.ranges]
    )
    expected_aspect = spans / spans.max()
    np.testing.assert_allclose(
        [viewport.aspectratio[axis] for axis in ("x", "y", "z")],
        expected_aspect,
    )
    # The largest final range defines one rendered cube half-span, so all
    # three data axes use the same data-units-per-cube-unit scale.
    np.testing.assert_allclose(viewport.cube_scale, spans.max() / 2.0)
    assert hash(viewport.signature)
    np.testing.assert_allclose(_axis_cube_scale(scene, style), viewport.cube_scale)


def test_layout_consumes_resolved_viewport_without_rederiving_ranges() -> None:
    scene = _scene()
    style = {"display_mode": "formula_unit", "atom_scale": 1.0}
    viewport = resolve_viewport(scene, style)

    layout = figure_axis_layout(scene, style, viewport=viewport)

    assert layout["xaxis"]["range"] == viewport.x_range
    assert layout["yaxis"]["range"] == viewport.y_range
    assert layout["zaxis"]["range"] == viewport.z_range
    assert layout["aspectmode"] == viewport.aspectmode
    assert layout["aspectratio"] == viewport.aspectratio


def test_viewport_signature_changes_when_ranges_change() -> None:
    style = {"display_mode": "formula_unit", "atom_scale": 1.0}
    original = resolve_viewport(_scene(), style)
    changed_scene = _scene()
    changed_scene["draw_atoms"].append(
        {"cart": [20.0, 0.0, 0.0], "atom_radius": 0.2}
    )

    changed = resolve_viewport(changed_scene, style)

    assert changed.signature != original.signature
    assert changed.ranges[0][1] > original.ranges[0][1]


def test_viewport_spec_deep_freezes_aspect_and_input_alias() -> None:
    aspect = {"x": 1.0, "y": 0.5, "z": 0.25}
    viewport = ViewportSpec(
        ranges=((0.0, 2.0), (0.0, 2.0), (0.0, 2.0)),
        aspectmode="manual",
        aspectratio=aspect,
        cube_scale=(1.0, 1.0, 1.0),
        signature=("test",),
    )

    aspect["x"] = 9.0
    assert viewport.aspectratio["x"] == 1.0
    with pytest.raises(TypeError):
        viewport.aspectratio["x"] = 9.0


def test_resolve_viewport_preserves_reversed_explicit_ranges() -> None:
    scene = _scene()
    scene["viewport"] = {
        "x": [1.0, -1.0],
        "y": [2.0, -2.0],
        "z": [3.0, -3.0],
    }
    style = {"display_mode": "formula_unit", "atom_scale": 1.0}

    viewport = resolve_viewport(scene, style)

    assert viewport.ranges == ((1.0, -1.0), (2.0, -2.0), (3.0, -3.0))
    assert viewport.aspectmode == "data"
    layout = figure_axis_layout(scene, style, viewport=viewport)
    assert layout["xaxis"]["range"] == (1.0, -1.0)
    np.testing.assert_allclose(viewport.cube_scale, (1.0, 2.0, 3.0))


def test_build_figure_reuses_one_viewport_for_compass(monkeypatch) -> None:
    from mat_viewer.render import figures

    scene = _scene()
    for index, atom in enumerate(scene["draw_atoms"], start=1):
        atom.update(
            {
                "label": f"C{index}",
                "elem": "C",
                "color": "#000000",
                "is_minor": False,
            }
        )
    scene.update(
        {
            "name": "resolver",
            "M": np.eye(3).tolist(),
            "bonds": [],
            "view_direction": [0.0, 0.0, 1.0],
            "up": [0.0, 1.0, 0.0],
        }
    )
    style = {
        "display_mode": "formula_unit",
        "atom_scale": 1.0,
        "bond_radius": 0.1,
        "show_axes": True,
        "show_axis_key": True,
        "topology_enabled": True,
    }
    original = figures.resolve_viewport
    resolved = []

    def spy(scene_arg, style_arg, topology_data=None):
        viewport = original(scene_arg, style_arg, topology_data=topology_data)
        resolved.append(viewport)
        return viewport

    monkeypatch.setattr(figures, "resolve_viewport", spy)
    figure = figures.build_figure(
        scene,
        style,
        topology_data={
            "center_coords": [0.0, 0.0, 0.0],
            "shell_coords": [[8.0, 0.0, 0.0]],
            "distances": [1.0],
        },
    )

    assert len(resolved) == 1
    viewport = resolved[0]
    assert figure.layout.scene.xaxis.range == viewport.x_range
    assert viewport.x_range[1] > 8.0
    assert figure.layout.meta["compass"]["cube_scale"] == list(viewport.cube_scale)
