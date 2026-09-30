"""Padded ranges must not stretch planar atom meshes or compass directions."""

import numpy as np
import pytest

from mat_viewer.compass import camera_screen_basis
from mat_viewer.render.viewport import (
    _axis_cube_scale,
    _camera_axis_projections,
    _scene_ranges,
    figure_axis_layout,
)


@pytest.mark.parametrize("mode", ["formula_unit", "asymmetric_unit", "cluster", "unit_cell"])
@pytest.mark.parametrize("height", [0.0, 1e-8, 0.7])
def test_padding_preserves_equal_cartesian_units(mode, height):
    scene = {
        "name": "synthetic-water", "display_mode": mode,
        "M": np.array([[8.0, 2.0, 0.0], [0.0, 12.0, 1.0], [0.0, 0.0, 5.0]]),
        "draw_atoms": [
            {"cart": [4, 4, 4], "atom_radius": .17, "is_minor": False},
            {"cart": [4.96, 4, 4 + height], "atom_radius": .16, "is_minor": False},
            {"cart": [3.76, 4.93, 4], "atom_radius": .16, "is_minor": False},
        ],
    }
    style = {"display_mode": mode, "show_unit_cell": False}
    ranges = _scene_ranges(scene, style)
    layout = figure_axis_layout(scene, style, *ranges)
    assert layout["aspectmode"] == "manual"
    spans = np.array([b - a for a, b in ranges])
    scale = np.array([layout["aspectratio"][axis] for axis in "xyz"]) / spans
    np.testing.assert_allclose(scale, np.repeat(scale[0], 3), rtol=1e-12)
    for axis, expected in zip(("xaxis", "yaxis", "zaxis"), ranges):
        assert layout[axis]["range"] == expected


def test_explicit_anisotropic_viewport_and_camera_remain_unchanged():
    viewport = {"x": [3.35, 5.37], "y": [3.58, 5.34], "z": [3.58, 4.42]}
    camera = {"eye": {"x": 1.0, "y": -2.0, "z": 3.0},
              "center": {"x": 0, "y": 0, "z": 0},
              "up": {"x": 0, "y": 1, "z": 0},
              "projection": {"type": "orthographic"}}
    scene = {"viewport": viewport, "display_mode": "formula_unit"}
    style = {"camera": camera}
    ranges = _scene_ranges(scene, style)
    layout = figure_axis_layout(scene, style, *ranges)
    assert layout["camera"] == camera
    assert layout["aspectmode"] == "manual"
    np.testing.assert_allclose(list(layout["aspectratio"].values()), [1, 1.76 / 2.02, .84 / 2.02])
    np.testing.assert_allclose(_axis_cube_scale(scene, style), [1.01, 1.01, 1.01])


def test_skew_lattice_compass_uses_same_isometric_mapping_as_geometry():
    lattice = np.array([[8, 3, 1], [2, 12, 4], [1, 2, 6]], dtype=float)
    scene = {"M": lattice, "display_mode": "formula_unit",
             "viewport": {"x": [-1, 3], "y": [-2, 6], "z": [-.5, .5]}}
    style = {"camera": {"eye": {"x": 1.0, "y": -2.0, "z": 3.0},
                        "up": {"x": 0, "y": 1, "z": 0}}}
    camera = figure_axis_layout(scene, style, *_scene_ranges(scene, style))["camera"]
    right, up = camera_screen_basis(camera)
    directions = lattice / np.linalg.norm(lattice, axis=1)[:, None]
    expected = np.column_stack((directions @ right, directions @ up))
    np.testing.assert_allclose(_camera_axis_projections(scene, style), expected, atol=1e-12)


def test_real_mesh_and_row_and_camera_patch_share_range_aspect(tmp_path):
    from ase import Atoms
    from ase.io import write
    from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
    from mat_viewer.presets import DEFAULT_STYLE
    from mat_viewer.renderer import build_figure
    from mat_viewer.render.figures import build_row_figure
    from mat_viewer.app.camera_helpers import _camera_figure_patch

    path = tmp_path / "synthetic-water.cif"
    write(path, Atoms("OH2", positions=[[4, 4, 4], [4.96, 4, 4], [3.76, 4.93, 4]],
                      cell=[8, 8, 8], pbc=True))
    bundle = build_loaded_crystal(name="water", cif_path=str(path), title="Synthetic water")
    scene = build_bundle_scene(bundle, display_mode="formula_unit", show_hydrogen=True)
    style = {**DEFAULT_STYLE, "display_mode": "formula_unit", "material": "mesh",
             "show_axes": False, "show_axis_key": False, "show_unit_cell": False}
    figure = build_figure(scene, style)
    assert any(trace.type == "mesh3d" and len(trace.x) for trace in figure.data)
    layout = figure.layout.scene.to_plotly_json()
    spans = np.array([np.diff(layout[f"{axis}axis"]["range"])[0] for axis in "xyz"])
    assert layout["aspectmode"] == "manual"
    np.testing.assert_allclose([layout["aspectratio"][axis] for axis in "xyz"], spans / max(spans))

    row = build_row_figure([(scene, style), (scene, style)])
    for panel in (row.layout.scene, row.layout.scene2):
        assert panel.aspectmode == "manual"
        assert panel.aspectratio.to_plotly_json() == layout["aspectratio"]

    patch = _camera_figure_patch(scene, style, layout["camera"]).to_plotly_json()
    values = {operation["location"][-1]: operation["params"]["value"]
              for operation in patch["operations"]}
    assert values["aspectmode"] == "manual"
    assert values["aspectratio"] == layout["aspectratio"]
    assert values["camera"] == layout["camera"]