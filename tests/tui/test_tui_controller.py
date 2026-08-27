from __future__ import annotations

import json

import numpy as np
import pytest

from mat_viewer.math.camera import Camera, ProjectionMode, project_points
from mat_viewer.tui.compositor import viewport_from_bounds
from mat_viewer.tui.controller import TerminalViewController
from mat_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR, Lattice


def _crystal() -> CrystalIR:
    return CrystalIR(
        title="controller",
        formula="CO",
        lattice=Lattice(
            a=10.0,
            b=10.0,
            c=10.0,
            alpha=90.0,
            beta=90.0,
            gamma=90.0,
            matrix=np.diag([10.0, 10.0, 10.0]),
        ),
        atoms=[
            AtomIR(
                "C",
                np.array([-1.0, 0.0, 0.0]),
                np.zeros(3),
                atom_id="site:C1",
                label="C1",
                index=0,
                display_copy_id="copy:C1",
            ),
            AtomIR(
                "O",
                np.array([1.0, 0.0, 0.0]),
                np.zeros(3),
                atom_id="site:O1",
                label="O1",
                index=1,
                display_copy_id="copy:O1",
            ),
        ],
        bonds=[BondIR(0, 1, 2.0)],
    )


def test_controller_observation_is_json_safe_and_detached() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)

    first = controller.observe()
    changed = controller.set_camera(azimuth=45.0, zoom=1.5)

    assert first.revision == 0
    assert changed.revision == 1
    assert first.state.camera.azimuth != changed.state.camera.azimuth
    assert changed.as_dict()["schema"] == "mattervis.tui.observation/v2"
    assert changed.as_dict()["frame"]["width"] == 40
    json.dumps(changed.as_dict(), allow_nan=False)
    _assert_no_forbidden_observation_keys(changed.as_dict())


def test_selection_is_visible_in_mono_and_survives_camera_rotation() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)

    selected = controller.select_atom("C1")
    rotated = controller.orbit(yaw_deg=90.0)

    assert selected.state.selection.atom_id == "site:C1"
    assert selected.state.selection.display_copy_id == "copy:C1"
    assert "[C1]" in selected.frame
    assert rotated.state.selection.atom_id == "site:C1"
    assert rotated.state.selection.display_index == 0
    assert "[C1]" in rotated.frame


def test_molecule_level_keeps_the_selected_atom_marker() -> None:
    controller = TerminalViewController(
        _crystal(), width=40, height=12, mono=True, display_level="molecule"
    )

    selected = controller.select_atom("C1")

    assert "[C1]" in selected.frame


def test_hit_map_and_atom_neighbor_traversal_are_deterministic() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)
    hits = controller.atom_hit_map()

    assert [(hit.atom_id, hit.display_copy_id) for hit in hits] == [
        ("site:C1", "copy:C1"),
        ("site:O1", "copy:O1"),
    ]
    picked = controller.select_screen(row=hits[0].row, col=hits[0].col)
    neighbor = controller.select_neighbor(step=1)
    cycled = controller.select_next(step=1)

    assert picked.state.selection.atom_id == "site:C1"
    assert neighbor.state.selection.atom_id == "site:O1"
    assert cycled.state.selection.atom_id == "site:C1"


def test_directional_selection_uses_current_projected_atom_positions() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)
    hits = controller.atom_hit_map()
    left, right = hits
    controller.select_screen(row=left.row, col=left.col)
    delta_col = right.col - left.col
    delta_row = right.row - left.row

    if abs(delta_col) >= abs(delta_row):
        moved = controller.select_direction(dx=1 if delta_col > 0 else -1)
    else:
        moved = controller.select_direction(dy=1 if delta_row > 0 else -1)

    assert moved.state.selection.atom_id == "site:O1"


def test_selection_mode_pin_and_clear_are_explicit_state_transitions() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)

    active = controller.set_selection_mode(True)
    pinned = controller.pin_selection()
    cleared = controller.clear_selection()

    assert active.state.selection.mode is True
    assert active.state.selection.atom_id == "site:C1"
    assert pinned.state.selection.mode is False
    assert pinned.state.selection.pinned is True
    assert cleared.state.selection.display_index is None
    assert "[C1]" not in cleared.frame


def test_controller_orbit_keeps_explicit_fit_bounds_stable() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)
    before = controller.state.viewport

    controller.orbit(yaw_deg=45.0, pitch_deg=15.0)
    after_orbit = controller.state.viewport
    controller.set_display(show_cell=False, show_bonds=False, label_mode="dot")
    after_display = controller.state.viewport

    assert after_orbit == before
    assert after_display == before


def test_rotation_invariant_all_fit_keeps_elongated_structure_visible() -> None:
    crystal = CrystalIR(
        atoms=[
            AtomIR("C", np.array([-100.0, 0.0, 0.0]), np.zeros(3), label="C1", index=0),
            AtomIR("C", np.array([100.0, 0.0, 0.0]), np.zeros(3), label="C2", index=1),
        ]
    )
    controller = TerminalViewController(
        crystal, width=80, height=24, mono=True, show_cell=False
    )

    for yaw_deg, pitch_deg in ((0.0, 0.0), (90.0, 0.0), (45.0, 30.0), (135.0, -30.0)):
        controller.set_camera(azimuth=0.0, elevation=0.0)
        controller.orbit(yaw_deg=yaw_deg, pitch_deg=pitch_deg)
        viewport = controller.observe().state.viewport
        points, _ = project_points(controller.camera, crystal.cart_coords)
        assert np.all(points[:, 0] >= viewport.x_min)
        assert np.all(points[:, 0] <= viewport.x_max)
        assert np.all(points[:, 1] >= viewport.y_min)
        assert np.all(points[:, 1] <= viewport.y_max)


def test_controller_fit_is_rotation_invariant_and_resize_updates_scale() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)
    initial = controller.state.viewport

    controller.orbit(yaw_deg=90.0)
    controller.fit()
    fitted = controller.state.viewport
    controller.resize(80, 24)
    resized = controller.state.viewport

    assert fitted == initial
    assert resized.width == 80
    assert resized.height == 24
    assert resized != fitted


def test_resize_viewport_keeps_fit_bounds() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12, mono=True)
    controller.orbit(yaw_deg=90.0)
    controller.fit()
    before = controller.state.viewport

    controller.resize_viewport(80, 24)
    after = controller.state.viewport

    assert (after.x_min, after.x_max, after.y_min, after.y_max) == pytest.approx(
        (before.x_min, before.x_max, before.y_min, before.y_max)
    )
    assert (after.width, after.height) == (80, 24)
    actual = viewport_from_bounds(
        before.x_min,
        before.x_max,
        before.y_min,
        before.y_max,
        80,
        24,
    )
    assert after.scale == pytest.approx(actual.scale)


def test_controller_display_transitions_are_absolute() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12)

    observation = controller.set_display(
        display_level="molecule",
        label_mode="element",
        show_bonds=False,
        show_cell=False,
        show_minor=True,
        mono=True,
    )

    assert observation.revision == 1
    assert observation.state.display.as_dict() == {
        "display_level": "molecule",
        "label_mode": "element",
        "show_bonds": False,
        "show_cell": False,
        "show_minor": True,
        "mono": True,
    }


def test_display_update_is_atomic_when_a_later_field_is_invalid() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12)
    before = controller.state

    with pytest.raises(ValueError, match="label_mode"):
        controller.set_display(show_bonds=False, label_mode="invalid")

    assert controller.state == before


def test_camera_target_is_serialized_and_updates_projection_state() -> None:
    controller = TerminalViewController(_crystal(), width=40, height=12)

    observation = controller.set_camera(target=[1.0, 2.0, 3.0])

    assert observation.state.camera.target == pytest.approx((1.0, 2.0, 3.0))
    assert observation.as_dict()["camera"]["target"] == [1.0, 2.0, 3.0]


def test_controller_reset_restores_camera_but_not_display() -> None:
    initial = Camera(
        azimuth=5.0,
        elevation=10.0,
        projection=ProjectionMode.PERSPECTIVE,
        viewport_zoom=1.5,
    )
    controller = TerminalViewController(_crystal(), camera=initial, width=40, height=12)
    controller.orbit(yaw_deg=30.0)
    controller.set_display(show_bonds=False, label_mode="dot")

    reset = controller.reset_view()

    assert reset.state.camera.azimuth == pytest.approx(initial.azimuth)
    assert reset.state.camera.elevation == pytest.approx(initial.elevation)
    assert reset.state.camera.projection == "perspective"
    assert reset.state.camera.zoom == pytest.approx(initial.viewport_zoom)
    assert reset.state.display.show_bonds is False
    assert reset.state.display.label_mode == "dot"


def test_controller_rejects_invalid_inputs_without_state_change() -> None:
    controller = TerminalViewController(_crystal())
    before = controller.state

    with pytest.raises(ValueError, match="projection"):
        controller.set_camera(projection="fisheye")
    with pytest.raises(ValueError, match="label_mode"):
        controller.set_display(label_mode="unknown")
    with pytest.raises(ValueError, match="greater than zero"):
        controller.zoom(factor=0)

    assert controller.state == before


def test_controller_alignment_requires_lattice() -> None:
    controller = TerminalViewController(CrystalIR(atoms=_crystal().atoms))

    with pytest.raises(ValueError, match="requires a crystal lattice"):
        controller.align("c")


def _assert_no_forbidden_observation_keys(value) -> None:
    forbidden = {
        "depth",
        "distance",
        "front",
        "collision",
        "score",
        "best_camera",
        "cartesian",
        "fractional",
    }
    if isinstance(value, dict):
        assert not (set(value) & forbidden)
        for nested in value.values():
            _assert_no_forbidden_observation_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_forbidden_observation_keys(nested)
