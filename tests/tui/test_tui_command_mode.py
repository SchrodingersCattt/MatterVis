from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from crystal_viewer.tui.app import CrystalTUI
from crystal_viewer.tui.controller import TerminalViewController
from crystal_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR, Lattice


ROOT = Path(__file__).resolve().parents[2]
DIRTY_VASP = Path(__file__).parent / "fixtures" / "dirty_geometry.vasp"


def _measurement_crystal() -> CrystalIR:
    matrix = np.diag([10.0, 10.0, 10.0])
    atoms = [
        AtomIR("C", np.array([9.0, 0.0, 0.0]), np.array([0.9, 0.0, 0.0]), label="A", index=0, source_index=0, display_copy_id="A/source:0/image:0,0,0"),
        AtomIR("C", np.array([0.0, 0.0, 0.0]), np.zeros(3), label="B", index=1, source_index=1, display_copy_id="B/source:1/image:0,0,0"),
        AtomIR("C", np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.1, 0.0]), label="C", index=2, source_index=2, display_copy_id="C/source:2/image:0,0,0"),
        AtomIR("C", np.array([0.0, 1.0, 1.0]), np.array([0.0, 0.1, 0.1]), label="D", index=3, source_index=3, display_copy_id="D/source:3/image:0,0,0"),
    ]
    return CrystalIR(
        atoms=atoms,
        bonds=[BondIR(0, 1, 9.0), BondIR(1, 2, 1.0), BondIR(2, 3, 1.0)],
        lattice=Lattice(10.0, 10.0, 10.0, 90.0, 90.0, 90.0, matrix),
    )


def test_measurement_primitives_support_direct_mic_angle_and_dihedral() -> None:
    controller = TerminalViewController(_measurement_crystal())
    before = controller.state

    direct = controller.measure_distance(["A", "B"], mode="direct")
    mic = controller.measure_distance(["A", "B"], mode="mic")
    angle = controller.measure_angle(["A", "B", "C"])
    dihedral = controller.measure_dihedral(["A", "B", "C", "D"])

    assert controller.state == before
    assert direct["value"] == pytest.approx(9.0)
    assert mic["value"] == pytest.approx(1.0)
    assert mic["image_shifts"] == [[0, 0, 0], [1, 0, 0]]
    assert angle["value"] == pytest.approx(90.0)
    assert dihedral["value"] == pytest.approx(90.0)


def test_measurements_use_true_nearest_image_in_skewed_cell() -> None:
    matrix = np.array([[10.0, 0.0, 0.0], [9.0, 1.0, 0.0], [0.0, 0.0, 10.0]])
    crystal = CrystalIR(
        atoms=[
            AtomIR("C", np.zeros(3), np.zeros(3), label="A", display_copy_id="A"),
            AtomIR("C", np.array([9.31, 0.49, 0.0]), np.array([0.49, 0.49, 0.0]), label="B", display_copy_id="B"),
        ],
        lattice=Lattice(10.0, np.sqrt(82.0), 10.0, 90.0, 90.0, 6.34, matrix),
    )
    controller = TerminalViewController(crystal)

    result = controller.measure_distance(["A", "B"], mode="mic")

    assert result["value"] == pytest.approx(np.linalg.norm([0.31, -0.51, 0.0]))
    assert result["image_shifts"] == [[0, 0, 0], [0, -1, 0]]


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (np.array([2.0, 1.0, 0.0]), 0.0),
        (np.array([2.0, -1.0, 0.0]), 180.0),
        (np.array([2.0, 0.0, 1.0]), 90.0),
        (np.array([2.0, 0.0, -1.0]), -90.0),
    ],
)
def test_signed_dihedral_covers_cis_trans_and_handedness(d, expected) -> None:
    atoms = [
        AtomIR("C", np.array([0.0, 1.0, 0.0]), np.zeros(3), label="A", display_copy_id="A"),
        AtomIR("C", np.array([0.0, 0.0, 0.0]), np.zeros(3), label="B", display_copy_id="B"),
        AtomIR("C", np.array([1.0, 0.0, 0.0]), np.zeros(3), label="C", display_copy_id="C"),
        AtomIR("C", d, np.zeros(3), label="D", display_copy_id="D"),
    ]
    result = TerminalViewController(CrystalIR(atoms=atoms)).measure_dihedral(
        ["A", "B", "C", "D"], mode="direct"
    )
    assert result["value"] == pytest.approx(expected)


def test_local_focus_fits_center_and_bond_neighbors() -> None:
    controller = TerminalViewController(_measurement_crystal(), width=60, height=16)

    observation = controller.focus_local("B", bond_depth=1)

    assert observation.state.focus.kind == "local"
    assert observation.state.focus.framed_copy_ids == (
        "A/source:0/image:0,0,0",
        "B/source:1/image:0,0,0",
        "C/source:2/image:0,0,0",
    )
    assert observation.state.viewport.x_max - observation.state.viewport.x_min > 0.01


def test_local_focus_rejects_non_integer_depth_without_mutation() -> None:
    controller = TerminalViewController(_measurement_crystal())
    before = controller.state

    with pytest.raises(ValueError, match="non-negative integer"):
        controller.focus_local("B", bond_depth=1.5)  # type: ignore[arg-type]

    assert controller.state == before


def test_local_focus_rejects_hidden_center_even_with_visible_neighbor() -> None:
    crystal = _measurement_crystal()
    crystal.atoms[1].is_minor = True
    controller = TerminalViewController(crystal, show_minor=False)
    before = controller.state

    with pytest.raises(ValueError, match="no visible"):
        controller.focus_local("B", bond_depth=1)

    assert controller.state == before


def test_wrong_vasp_command_measurements_match_known_dirty_geometry() -> None:
    app = CrystalTUI(
        TerminalViewController.from_file(str(DIRTY_VASP)).crystal,
        mono=True,
        label_mode="label",
    )

    angle, _ = app.execute_command(":angle C12 N9 C13")
    direct, _ = app.execute_command(":distance Cd2 Cl3 direct")
    mic, _ = app.execute_command(":distance Cd2 Cl3 mic")
    focus_text, focus = app.execute_command(":focus N9 1")

    assert "46.0155 degree" in angle
    assert "5.4905 angstrom" in direct
    assert "2.6451 angstrom" in mic
    assert "focused N9 with bond depth 1" == focus_text
    assert focus is not None
    assert len(focus.state.focus.framed_copy_ids) == 5


def test_colon_opens_command_input_and_submitted_measurement_is_shown() -> None:
    async def exercise() -> None:
        app = CrystalTUI(_measurement_crystal(), mono=True)
        async with app.run_test(size=(60, 16)) as pilot:
            await pilot.pause()
            await pilot.press("shift+semicolon")
            await pilot.pause()
            command = app.query_one("#command")
            assert command.display is True
            await pilot.press(*list("distance A B mic"), "enter")
            await pilot.pause()
            assert not app.query("#command").nodes
            assert "1.0000 angstrom" in str(app.query_one("#command-result").render())

    asyncio.run(exercise())


def test_repeated_colon_keeps_one_command_input_and_escape_allows_reopen() -> None:
    async def exercise() -> None:
        app = CrystalTUI(_measurement_crystal(), mono=True)
        async with app.run_test(size=(60, 16)) as pilot:
            await pilot.pause()
            await pilot.press("shift+semicolon", "shift+semicolon")
            await pilot.pause()
            assert len(app.query("#command").nodes) == 1
            await pilot.press("escape")
            await pilot.pause()
            assert not app.query("#command").nodes
            await pilot.press("shift+semicolon")
            await pilot.pause()
            assert len(app.query("#command").nodes) == 1

    asyncio.run(exercise())