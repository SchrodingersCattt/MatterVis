"""Atom-level terminal picture: fit, occlusion, bonds, and label placement."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from mat_viewer.math.camera import Camera, project_points
from mat_viewer.tui.app import CrystalTUI
from mat_viewer.tui.compositor import compose_frame
from mat_viewer.tui.controller import TerminalViewController
from mat_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR, Lattice
from mat_viewer.tui.projection import CHAR_ASPECT, _compute_viewport


def _lattice(length: float = 10.0) -> Lattice:
    return Lattice(
        a=length, b=length, c=length, alpha=90.0, beta=90.0, gamma=90.0,
        matrix=np.diag([length, length, length]),
    )


def _atom(element, xyz, index, label=None, **kwargs) -> AtomIR:
    position = np.asarray(xyz, dtype=float)
    return AtomIR(
        element,
        position,
        position / 10.0,
        label=label or f"{element}{index + 1}",
        index=index,
        **kwargs,
    )


def _frame(crystal, *, width=80, height=24, **options) -> str:
    camera = Camera.from_view_name("c", crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    options = {"label_mode": "label", "display_level": "atom", **options}
    return compose_frame(
        crystal,
        camera,
        points,
        depth,
        width=width,
        height=height,
        mono=True,
        **options,
    )


def test_visible_atoms_fill_the_limiting_axis_without_the_cell() -> None:
    crystal = CrystalIR(
        lattice=_lattice(80.0),
        atoms=[
            _atom("C", (-2.0, 0.0, 0.0), 0),
            _atom("O", (2.0, 0.0, 0.0), 1),
            _atom("N", (0.0, 1.5, 0.5), 2),
        ],
        bonds=[BondIR(0, 1, 4.0)],
    )
    controller = TerminalViewController(
        crystal, width=100, height=30, mono=True, show_cell=True, label_mode="element"
    )
    state = controller.state.viewport
    cell_points, _ = project_points(controller.camera, crystal.lattice.cell_vertices())
    cell_view = _compute_viewport(cell_points, [], state.width, state.height)
    x_span = state.x_max - state.x_min
    y_span = state.y_max - state.y_min
    limiting = min((state.width - 1) / x_span, (state.height - 1) * CHAR_ASPECT / y_span)

    assert state.scale == pytest.approx(limiting)
    assert state.scale > cell_view.scale * 2


def test_occluded_atom_is_absent_and_bonds_differ_from_cell_edges() -> None:
    stacked = CrystalIR(
        atoms=[
            _atom("C", (0.0, 0.0, 0.0), 0, label="C1"),
            _atom("O", (0.0, 0.0, 0.0), 1, label="O1"),
        ],
    )
    hidden = _frame(stacked, show_cell=False, show_bonds=False, label_mode="element")
    assert "O" in hidden
    assert "C" not in hidden

    linked = CrystalIR(
        lattice=_lattice(),
        atoms=[
            _atom("C", (-2.0, 0.0, 0.0), 0),
            _atom("N", (2.0, 0.0, 0.0), 1),
        ],
        bonds=[BondIR(0, 1, 4.0, start=np.array([-2.0, 0.0, 0.0]), end=np.array([2.0, 0.0, 0.0]))],
    )
    frame = _frame(linked, show_cell=True, show_bonds=True)
    assert ":" in frame
    assert any(char in frame for char in "-|/\\")


def test_hydrogen_and_boundary_replicas_are_hidden_until_requested() -> None:
    crystal = CrystalIR(
        lattice=_lattice(),
        atoms=[
            _atom("C", (0.0, 0.0, 0.0), 0),
            _atom("H", (1.5, 0.0, 0.0), 1),
            _atom("O", (6.0, 0.0, 0.0), 2, is_boundary_replica=True),
        ],
        bonds=[BondIR(0, 1, 1.5)],
    )
    hidden = _frame(crystal, show_cell=False, show_bonds=True)
    shown = _frame(
        crystal, show_cell=False, show_bonds=True, show_hydrogen=True, show_boundary=True
    )

    assert "H" not in hidden
    assert "O" not in hidden
    assert "C" in hidden
    assert "H" in shown and "O" in shown


def test_overlapping_and_long_labels_stay_off_occupied_cells() -> None:
    crystal = CrystalIR(
        atoms=[
            _atom("C", (0.0, 0.0, 0.0), 0, label="VeryLongAtomLabel"),
            _atom("N", (0.2, 0.0, 0.0), 1, label="N1"),
            _atom("O", (0.4, 0.0, 0.0), 2, label="O1"),
        ],
    )
    frame = _frame(crystal, width=40, height=16, show_cell=False, show_bonds=False)
    lines = frame.splitlines()

    assert "VeryLong" not in frame
    occupied = {}
    for row, line in enumerate(lines):
        column = 0
        while column < len(line):
            if line.startswith("N1", column) or line.startswith("O1", column):
                for offset in range(2):
                    assert (row, column + offset) not in occupied
                    occupied[(row, column + offset)] = line[column:column + 2]
                column += 2
            else:
                column += 1


def test_resize_refits_the_same_atom_bounds_to_the_new_aspect() -> None:
    crystal = CrystalIR(atoms=[_atom("C", (-1.0, 0.0, 0.0), 0), _atom("O", (1.0, 0.0, 0.0), 1)])
    controller = TerminalViewController(crystal, width=40, height=12, mono=True, show_cell=False)
    before = controller.state.viewport
    controller.resize_viewport(80, 24)
    after = controller.state.viewport

    assert (after.x_min, after.x_max, after.y_min, after.y_max) == (
        before.x_min, before.x_max, before.y_min, before.y_max
    )
    assert after.width == 80 and after.height == 24
    assert after.scale != before.scale


def test_hydrogen_key_toggles_the_atom_picture() -> None:
    crystal = CrystalIR(
        atoms=[_atom("C", (0.0, 0.0, 0.0), 0), _atom("H", (1.2, 0.0, 0.0), 1)],
        bonds=[BondIR(0, 1, 1.2)],
    )

    async def exercise() -> None:
        app = CrystalTUI(crystal, mono=True, label_mode="element", show_cell=False)
        async with app.run_test(size=(60, 18)) as pilot:
            await pilot.pause()
            assert "H" not in app.query_one("#canvas").frame_text
            await pilot.press("h")
            await pilot.pause()
            assert app.controller.state.display.show_hydrogen is True
            assert "H" in app.query_one("#canvas").frame_text

    asyncio.run(exercise())


def test_multi_character_atom_is_not_erased_by_its_own_bond() -> None:
    """Cl bonded rightward toward a closer neighbour must keep both letters."""
    crystal = CrystalIR(
        atoms=[
            _atom("Cl", (0.0, 0.0, 0.0), 0),
            _atom("O", (3.0, 0.0, 0.5), 1),
        ],
        bonds=[
            BondIR(
                0,
                1,
                3.0,
                start=np.array([0.0, 0.0, 0.0]),
                end=np.array([3.0, 0.0, 0.5]),
            )
        ],
    )
    frame = _frame(crystal, show_cell=False, show_bonds=True, label_mode="element")
    assert "Cl" in frame
    assert "O" in frame


def test_place_keeps_atom_kind_for_colour_and_occlusion() -> None:
    from mat_viewer.tui.glyph_frame import _Grid

    grid = _Grid(8, 3, mono=False)
    assert grid.place(1, 2, "C", 1.0, rgb=(255, 0, 0), kind="atom", anchor=0)
    assert grid.kind[1][2] == "atom"
    # A farther atom must not overwrite the closer one once kind is stored.
    assert not grid.place(1, 2, "N", 0.0, rgb=(0, 255, 0), kind="atom", anchor=1)
    assert grid.chars[1][2] == "C"
    painted = grid.to_string()
    assert "\033[" in painted


def test_static_fit_ignores_hidden_hydrogen_and_boundary() -> None:
    crystal = CrystalIR(
        atoms=[
            _atom("C", (0.0, 0.0, 0.0), 0),
            _atom("N", (1.4, 0.0, 0.0), 1),
            _atom("O", (30.0, 0.0, 0.0), 2, is_boundary_replica=True),
            _atom("H", (-30.0, 0.0, 0.0), 3),
        ],
        bonds=[BondIR(0, 1, 1.4)],
    )
    frame = _frame(
        crystal, width=80, height=12, show_cell=False, show_bonds=True, label_mode="element"
    )
    # With the distant hidden atoms filtered from the fit, C--N fills most columns.
    assert "C" in frame and "N" in frame
    assert "O" not in frame and "H" not in frame
    occupied = max((len(line.strip()) for line in frame.splitlines()), default=0)
    assert occupied > 20


def test_element_and_dot_modes_mark_the_selected_atom() -> None:
    crystal = CrystalIR(
        atoms=[_atom("C", (0.0, 0.0, 0.0), 0), _atom("O", (2.0, 0.0, 0.0), 1)],
    )
    plain = _frame(crystal, show_cell=False, show_bonds=False, label_mode="element")
    selected = _frame(
        crystal,
        show_cell=False,
        show_bonds=False,
        label_mode="element",
        selected_display_index=0,
    )
    assert "[C]" in selected
    assert selected != plain
    dotted = _frame(
        crystal,
        show_cell=False,
        show_bonds=False,
        label_mode="dot",
        selected_display_index=1,
        charset="ascii7",
    )
    assert "[*]" in dotted


def test_auto_label_mode_ignores_hidden_hydrogen_density() -> None:
    """Hidden H must not push auto labels to dots when only heavies are drawn."""
    heavies = [
        _atom("C", (0.0, 0.0, 0.0), 0),
        _atom("N", (1.5, 0.0, 0.0), 1),
        _atom("O", (3.0, 0.0, 0.0), 2),
    ]
    hydrogens = [_atom("H", (i * 0.1, 4.0, 0.0), 3 + i) for i in range(200)]
    crystal = CrystalIR(atoms=[*heavies, *hydrogens])
    frame = _frame(
        crystal,
        width=80,
        height=24,
        show_cell=False,
        show_bonds=False,
        label_mode="auto",
    )
    assert "●" not in frame
    assert "C" in frame and "N" in frame and "O" in frame
    assert "H" not in frame
