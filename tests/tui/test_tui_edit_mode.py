from __future__ import annotations

import numpy as np
import pytest

from crystal_viewer.tui.app import CrystalTUI
from crystal_viewer.tui.controller import TerminalViewController
from crystal_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR
from crystal_viewer.tui.state import (
    TerminalCameraState,
    TerminalDisplayState,
    TerminalFocusState,
    TerminalObservation,
    TerminalViewportState,
    TerminalViewState,
)


def _crystal(*, molecules: bool = False) -> CrystalIR:
    atoms = [
        AtomIR("N", np.array([0.0, 0.0, 0.0]), np.zeros(3), label="N1", index=0, source_index=0, display_copy_id="copy:N1", molecule_index=0 if molecules else -1, display_fragment_id="frag:0" if molecules else ""),
        AtomIR("C", np.array([1.0, 0.0, 0.0]), np.zeros(3), label="C2", index=1, source_index=1, display_copy_id="copy:C2", molecule_index=0 if molecules else -1, display_fragment_id="frag:0" if molecules else ""),
        AtomIR("O", np.array([0.0, 1.0, 0.0]), np.zeros(3), label="O3", index=2, source_index=2, display_copy_id="copy:O3", molecule_index=1 if molecules else -1, display_fragment_id="frag:1" if molecules else ""),
    ]
    return CrystalIR(
        atoms=atoms,
        bonds=[BondIR(0, 1, 1.0), BondIR(0, 2, 1.0)],
        species_map={"CN_1": [0], "O_1": [1]} if molecules else {},
    )


def test_atom_edit_tokens_are_visible_and_pick_uses_stable_identity() -> None:
    controller = TerminalViewController(_crystal(), width=60, height=16, mono=True)

    observation = controller.enter_edit("atom")
    assert observation.state.edit.as_dict() == {
        "mode": "edit", "level": "atom", "selected_ids": [], "active_id": None,
    }
    assert len(observation.pick_tokens) == 3
    assert all(f"[{token.token}]" in observation.frame for token in observation.pick_tokens)

    first, second = observation.pick_tokens[:2]
    selected = controller.pick([first.token, second.token])
    assert selected.state.edit.selected_ids == (first.target_id, second.target_id)
    assert selected.state.edit.active_id == second.target_id
    assert f">[{second.token}]" in selected.frame

    controller.orbit(yaw_deg=20.0)
    assert controller.state.edit.selected_ids == (first.target_id, second.target_id)


def test_pick_validation_is_atomic_and_active_must_be_selected() -> None:
    controller = TerminalViewController(_crystal(), width=60, height=16)
    observation = controller.enter_edit("atom")
    before = controller.state

    with pytest.raises(ValueError, match="unknown pick token"):
        controller.pick([observation.pick_tokens[0].token, "a999"])
    assert controller.state == before

    with pytest.raises(ValueError, match="already be selected"):
        controller.set_active(observation.pick_tokens[0].token)
    assert controller.state == before


def test_unpick_toggle_clear_and_focus_selection() -> None:
    controller = TerminalViewController(_crystal(), width=60, height=16)
    observation = controller.enter_edit("atom")
    tokens = [token.token for token in observation.pick_tokens]

    controller.pick(tokens[:2])
    controller.unpick([tokens[0]])
    assert len(controller.state.edit.selected_ids) == 1
    controller.toggle_pick([tokens[0], tokens[1]])
    assert len(controller.state.edit.selected_ids) == 1
    focused = controller.focus_edit_selection()
    assert focused.state.focus.kind == "selection"
    controller.clear_selection()
    assert controller.state.edit.selected_ids == ()


def test_edit_commands_are_controller_backed() -> None:
    app = CrystalTUI(_crystal(), mono=True)

    _, edit = app.execute_command(":edit atom")
    assert edit is not None
    token = edit.pick_tokens[0].token
    _, picked = app.execute_command(f":pick {token}")
    assert picked is not None
    assert len(picked.state.edit.selected_ids) == 1
    _, focused = app.execute_command(":focus selection")
    assert focused is not None
    _, exited = app.execute_command(":exit")
    assert exited is not None
    assert exited.state.edit.mode == "browse"


def test_current_atom_token_can_focus_its_visual_neighborhood() -> None:
    app = CrystalTUI(_crystal(), mono=True)
    _, edit = app.execute_command(":edit atom")
    assert edit is not None
    center = next(token for token in edit.pick_tokens if token.label == "N1")

    _, focused = app.execute_command(f":focus {center.token} 1")

    assert focused is not None
    assert focused.state.focus.kind == "local"
    assert set(focused.state.focus.framed_copy_ids) == {"copy:N1", "copy:C2", "copy:O3"}


def test_molecule_edit_uses_fragment_identity_and_rejects_missing_identity() -> None:
    controller = TerminalViewController(_crystal(molecules=True), width=60, height=16)
    observation = controller.enter_edit("molecule")

    assert {token.target_id for token in observation.pick_tokens} == {"frag:0", "frag:1"}
    picked = controller.pick([observation.pick_tokens[0].token])
    assert picked.state.edit.selected_ids[0] in {"frag:0", "frag:1"}

    unsupported = TerminalViewController(_crystal())
    with pytest.raises(ValueError, match="requires displayed molecule identities"):
        unsupported.enter_edit("molecule")


def test_crowded_atom_edit_keeps_every_pick_token_visible_without_wrapping() -> None:
    controller = TerminalViewController.from_file(
        "tests/tui/fixtures/dirty_geometry.vasp",
        width=80,
        height=22,
        mono=True,
        label_mode="label",
    )

    observation = controller.enter_edit("atom")

    assert len(observation.pick_tokens) == controller.crystal.n_atoms
    assert {token.label for token in observation.pick_tokens} >= {
        "N9", "N10", "C11", "C12", "C13", "C14", "C15", "C16", "C17", "C18",
    }
    assert all(f"[{token.token}]" in observation.frame for token in observation.pick_tokens)
    assert max(map(len, observation.frame.splitlines())) <= 80


@pytest.mark.parametrize(("width", "height", "mono"), [(12, 6, True), (40, 4, True), (40, 4, False)])
def test_edit_tokens_only_report_visible_entries_for_narrow_or_color_frames(
    width: int,
    height: int,
    mono: bool,
) -> None:
    controller = TerminalViewController.from_file(
        "tests/tui/fixtures/dirty_geometry.vasp",
        width=width,
        height=height,
        mono=mono,
        label_mode="label",
    )

    observation = controller.enter_edit("atom")
    plain_frame = _strip_ansi(observation.frame)

    assert len(observation.pick_tokens) <= controller.crystal.n_atoms
    assert all(f"[{token.token}]" in plain_frame for token in observation.pick_tokens)
    assert max(map(len, plain_frame.splitlines()), default=0) <= width
    assert observation.frame.count("\x1b[") % 2 == 0


def test_public_state_dataclasses_keep_old_positional_constructor_order() -> None:
    camera = TerminalCameraState(0.0, 0.0, 0.0, (0.0, 0.0, 0.0), "orthographic", 1.0, 0.0, 0.0)
    display = TerminalDisplayState("atom", "label", True, True, False, True)
    focus = TerminalFocusState()
    viewport = TerminalViewportState(80, 22, 1.0, -1.0, 1.0, -1.0, 1.0)

    state = TerminalViewState(0, camera, display, focus, viewport)
    observation = TerminalObservation(0, state, "title", "frame", {}, (), ("legacy-warning",), "legacy-schema")

    assert state.edit.mode == "browse"
    assert observation.warnings == ("legacy-warning",)
    assert observation.schema == "legacy-schema"
    assert observation.pick_tokens == ()


def _strip_ansi(value: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", value)