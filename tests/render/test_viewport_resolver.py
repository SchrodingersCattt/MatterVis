"""Contract tests for the shared rendering viewport resolver."""

from __future__ import annotations

import numpy as np

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
