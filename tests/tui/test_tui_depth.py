"""Picture-level acceptance for depth, occlusion, packing, and layer controls."""

import asyncio
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from mat_viewer.math.camera import Camera
from mat_viewer.tui.app import CrystalTUI
from mat_viewer.tui.compositor import compose_frame
from mat_viewer.tui.controller import TerminalViewController
from mat_viewer.tui.crystal_ir import AtomIR, CrystalIR, Lattice
from mat_viewer.tui.glyph_frame import _draw_atoms, _Grid
from mat_viewer.tui.projection import _compute_viewport
from mat_viewer.tui.session import TerminalAction, TerminalSession, run_jsonl_session


def _crystal() -> CrystalIR:
    return CrystalIR(
        lattice=Lattice(10, 10, 10, 90, 90, 90, np.diag([10.0, 10.0, 10.0])),
        atoms=[
            AtomIR(
                element,
                np.array(xyz),
                np.array(xyz) / 10,
                label=f"{element}{i + 1}",
                index=i,
                atom_id=f"site:{i}",
            )
            for i, (element, xyz) in enumerate(
                [("C", (0.5, 0.0, 0.0)), ("N", (1.5, 2.0, 1.0)), ("O", (3.5, 0.0, 2.0))]
            )
        ],
    )


@pytest.mark.parametrize(
    "charset,markers", [("unicode", ("¹", "²", "³")), ("ascii7", ("^1", "^2", "^3"))]
)
def test_depth_bands_survive_plain_text_and_ansi(charset, markers) -> None:
    points = np.array([[-1.0, 0.0], [0.0, 0.0], [1.0, 0.0]])
    crystal = _crystal()
    for mono in (True, False):
        frame = compose_frame(
            crystal,
            Camera(),
            points,
            np.array([1.0, 0.0, -1.0]),
            width=80,
            height=20,
            mono=mono,
            show_cell=False,
            show_bonds=False,
            show_axes=False,
            charset=charset,
        )
        assert all(
            f"{element}{i + 1}{marker}" in frame
            for i, (element, marker) in enumerate(zip("CNO", markers))
        )
        if not mono:
            assert "\033[1;" in frame and "\033[2;" in frame


def test_slice_highlight_does_not_override_back_band_dim_style() -> None:
    crystal = _crystal()
    crystal.atoms = crystal.atoms[:2]
    grid = _Grid(12, 4, mono=False)
    grid.depth_range = (-1.0, 1.0)
    grid.slice_distance = np.array([0, 0])
    viewport = SimpleNamespace(to_grid=lambda x, y: (int(y), int(x)))

    _draw_atoms(
        grid,
        crystal,
        np.array([[2.0, 1.0], [8.0, 1.0]]),
        np.array([-1.0, 0.0]),
        viewport,
        [0, 1],
        label_mode="element",
        charset="ascii7",
        selected_display_index=None,
    )

    assert grid.styles[1][2] == "dim"  # back-band atom remains visually recessed
    assert grid.styles[1][7] == "bold"  # middle-band slice stays highlighted


def test_occlusion_drops_whole_tokens_and_their_labels() -> None:
    crystal = _crystal()
    crystal.atoms = crystal.atoms[:2]
    crystal.atoms[0].element, crystal.atoms[0].label = "Cl", "ClHidden"
    frame = compose_frame(
        crystal,
        Camera(),
        np.zeros((2, 2)),
        np.array([-1.0, 1.0]),
        width=40,
        height=12,
        mono=True,
        show_cell=False,
        show_axes=False,
    )
    assert "N2¹" in frame
    assert "Cl" not in frame and "Hidden" not in frame


@pytest.mark.parametrize("reverse", [False, True])
def test_crossing_bond_break_is_order_independent(reverse) -> None:
    grid = _Grid(20, 15, mono=True)
    grid.show_depth = False
    lines = [(7, 1, 7, 17, 0.0, 0.0, (0, 1)), (1, 9, 13, 9, 2.0, 2.0, (2, 3))]
    for r0, c0, r1, c1, d0, d1, owners in reversed(lines) if reverse else lines:
        grid.stroke(
            r0,
            c0,
            r1,
            c1,
            d0,
            d1,
            text=None,
            rgb=(150, 150, 150),
            kind="bond",
            owners=owners,
        )
    grid.resolve_strokes()
    assert grid.chars[7][7:12] == ["-", " ", "|", " ", "-"]


@pytest.mark.parametrize("reverse", [False, True])
def test_diagonal_bonds_break_at_half_cell_crossings(reverse) -> None:
    grid = _Grid(12, 12, mono=True)
    grid.show_depth = False
    lines = [(1, 1, 10, 10, 0.0, (0, 1)), (1, 10, 10, 1, 2.0, (2, 3))]
    for r0, c0, r1, c1, depth, owners in reversed(lines) if reverse else lines:
        grid.stroke(
            r0,
            c0,
            r1,
            c1,
            depth,
            depth,
            text=None,
            rgb=(150, 150, 150),
            kind="bond",
            owners=owners,
        )
    grid.resolve_strokes()
    assert grid.chars[5][5] == " " and grid.chars[6][6] == " "
    assert grid.chars[5][6] == "/" and grid.chars[6][5] == "/"


def test_back_bonds_are_dashed_and_clipped_depths_are_interpolated() -> None:
    grid = _Grid(11, 3, mono=True)
    grid.depth_range = (0, 10)
    grid.stroke(1, -100, 1, 100, -10, 10, text=None, rgb=(150, 150, 150), kind="bond")
    grid.resolve_strokes()
    assert grid.depth[1][0] == pytest.approx(0)
    assert grid.depth[1][1] == pytest.approx(0.1)
    assert grid.chars[1][2:4] == [" ", " "]
    assert grid.chars[1][4] == "-"


def test_labels_cannot_overwrite_atoms_bonds_or_other_labels() -> None:
    grid = _Grid(30, 8, mono=True)
    grid.place(2, 4, "Cl1¹", 1.0, rgb=(0, 255, 0), kind="atom", anchor=0)
    grid.stroke(4, 4, 4, 20, 0.0, 0.0, text=None, rgb=(150, 150, 150), kind="bond")
    grid.resolve_strokes()
    assert not grid.place_in_gaps(2, 3, "overlap", anchor=1)
    assert not grid.place_in_gaps(4, 8, "overlap", anchor=1)
    assert grid.place_in_gaps(6, 3, "free", anchor=1)
    assert not grid.place_in_gaps(6, 5, "overlap", anchor=2)
    assert "Cl1¹" in grid.to_string() and "overlap" not in grid.to_string()


def test_molecule_surfaces_hide_back_hulls_and_mark_each_visible_instance() -> None:
    crystal = CrystalIR(
        atoms=[
            AtomIR("C", np.zeros(3), np.zeros(3), index=i, molecule_index=i // 3)
            for i in range(9)
        ],
        species_map={"C3_1": [0, 1, 2]},
    )
    triangle = np.array([[-1.0, -1.0], [1.0, -1.0], [0.0, 1.0]])
    points = np.vstack([triangle, triangle, triangle + [4.0, 0.0]])
    frame = compose_frame(
        crystal,
        Camera(),
        points,
        np.repeat([-1.0, 1.0, 0.0], 3),
        width=100,
        height=30,
        mono=True,
        display_level="molecule",
        show_cell=False,
        show_axes=False,
    )
    assert "M0" not in frame
    assert "M1¹" in frame and "M2²" in frame
    assert frame.count("C3") == 2


def test_layer_slice_marks_neighbours_hides_other_layers_and_restores_state() -> None:
    controller = TerminalViewController(
        _crystal(),
        width=80,
        height=24,
        mono=True,
        show_cell=False,
        show_axes=False,
        label_mode="label",
    )
    result = controller.set_slice(axis="a", index=0, width=0.1)
    assert "C1" in result.frame and "(N2" in result.frame and "O3" not in result.frame
    assert {hit.display_index for hit in controller.atom_hit_map()} == {0, 1}
    controller.save_view("layer")
    assert "O3" in controller.step_slice(step=2).frame
    assert controller.restore_view("layer").frame == result.frame
    before = controller.state
    with pytest.raises(ValueError):
        controller.set_slice(axis="b", width=0)
    assert controller.state == before
    assert "O3" in controller.set_slice().frame


def test_parallax_sequence_is_bounded_replayable_and_saved() -> None:
    def sequence():
        session = TerminalSession(
            _crystal(), width=80, height=24, charset="ascii7", show_cell=False
        )
        session.execute(TerminalAction("set_display", {"parallax": True}))
        frames = session.execute(
            TerminalAction("rotation_sequence", {"frames": 3, "yaw_deg": 10})
        )
        assert len(frames) == 3 and len({f.screen_hash for f in frames}) == 3
        assert all(
            len(f.screen_lines) == 24
            and all(len(line) == 80 and line.isascii() for line in f.screen_lines)
            for f in frames
        )
        assert "." in "".join(frames[-1].screen_lines[:-1])
        saved = session.controller.observe().frame
        session.controller.save_view("ghost")
        session.controller.orbit(yaw_deg=20)
        assert session.controller.restore_view("ghost").frame == saved
        revision = session.controller.revision
        with pytest.raises(ValueError):
            session.execute(TerminalAction("rotation_sequence", {"frames": 13}))
        assert session.controller.revision == revision
        return [f.screen_hash for f in frames]

    assert sequence() == sequence()


def test_jsonl_sequence_returns_individually_rendered_frames() -> None:
    output = io.StringIO()
    run_jsonl_session(
        TerminalSession(_crystal(), charset="ascii7"),
        input_stream=io.StringIO(
            '{"action":"rotation_sequence","arguments":{"frames":2}}\n'
        ),
        output_stream=output,
    )
    response = json.loads(output.getvalue())
    assert response["ok"] and not response["closed"]
    assert len(response["observations"]) == 2
    assert (
        response["observations"][0]["frame"]["sha256"]
        != response["observations"][1]["frame"]["sha256"]
    )


def test_fit_fills_canvas_after_orbit_and_resize() -> None:
    controller = TerminalViewController(_crystal(), width=160, height=48, mono=True)
    for action in (
        lambda: controller.observe(),
        lambda: controller.orbit(yaw_deg=35),
        lambda: controller.resize_viewport(90, 30),
    ):
        action()
        from mat_viewer.math.camera import project_points

        points, _ = project_points(controller.camera, controller.crystal.cart_coords)
        viewport = controller._effective_viewport()
        cells = np.array([viewport.to_grid(*p) for p in points])
        assert (
            max(
                np.ptp(cells[:, 1]) / (viewport.width - 1),
                np.ptp(cells[:, 0]) / (viewport.height - 1),
            )
            > 0.8
        )


def test_default_gizmo_and_keys_survive_warning_and_command_banners() -> None:
    async def exercise():
        app = CrystalTUI(_crystal(), mono=True)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await pilot.press("g", "f", "full_stop")
            await pilot.pause()
            assert app.controller.state.display.parallax
            assert app.controller.state.display.slice_axis == "depth"
            await pilot.press("colon")
            await pilot.pause()
            command = app.query_one("#command")
            command.value = "help"
            await pilot.press("enter")
            await pilot.pause()
            canvas = app.query_one("#canvas")
            assert canvas.frame_text.splitlines()[-1].startswith("axes a:")
            assert len(canvas.frame_text.splitlines()) == canvas.size.height
            await pilot.press("A")
            await pilot.pause()
            assert "axes a:" not in canvas.frame_text

    asyncio.run(exercise())
