"""Copy-aware labels, load-time neighbour shells and the axis footer."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.math.camera import Camera, project_points
from mat_viewer.tui.app import CrystalTUI
from mat_viewer.tui.compositor import LABEL_MODES, compose_frame, normalize_label_mode
from mat_viewer.tui.crystal_ir import AtomIR, BondIR, CrystalIR, Lattice
from mat_viewer.tui.loader_adapter import _transform_specs, load_for_tui
from mat_viewer.tui.session import TerminalAction, TerminalSession

COD_CIF = Path(__file__).resolve().parents[1] / "fixtures" / "cod_1000043.cif"


def _crystal() -> CrystalIR:
    def atom(label, x, operation, shift, index):
        return AtomIR(
            "O",
            np.array([x, 0.0, 0.0]),
            np.zeros(3),
            atom_id=f"site:{index}",
            label=label,
            index=index,
            symmetry_operation_index=operation,
            image_shift=shift,
            display_copy_id=f"copy:{index}",
        )

    return CrystalIR(
        title="copies",
        lattice=Lattice(
            a=8.0, b=8.0, c=8.0, alpha=90.0, beta=90.0, gamma=90.0,
            matrix=np.diag([8.0, 8.0, 8.0]),
        ),
        atoms=[
            atom("O1", -2.0, 0, (0, 0, 0), 0),
            atom("O1", 0.0, 1, (0, 0, 0), 1),
            atom("O1[1,0,0]", 2.0, 2, (1, 0, 0), 2),
        ],
        bonds=[BondIR(0, 1, 2.0)],
    )


def _session(**options) -> TerminalSession:
    return TerminalSession(
        _crystal(), width=60, height=14, charset="ascii7", display_level="atom", **options
    )


def test_label_modes_document_every_mode_and_alias_none_to_dot() -> None:
    assert normalize_label_mode("label_sym") == "label_sym"
    assert normalize_label_mode("none") == "dot"
    assert normalize_label_mode(" Label ") == "label"
    with pytest.raises(ValueError, match="label_mode must be one of") as error:
        normalize_label_mode("bogus")
    assert all(mode in str(error.value) for mode in LABEL_MODES)
    with pytest.raises(ValueError):
        normalize_label_mode(None)


def test_copy_aware_labels_distinguish_symmetry_and_lattice_copies() -> None:
    frame = _session(label_mode="label_sym").observe().observation.frame

    assert "O1#2" in frame
    assert "O1#3[1,0,0]" in frame
    assert "[1,0,0][1,0,0]" not in frame
    plain = _session(label_mode="label").observe().observation.frame
    assert "#" not in plain


def test_session_accepts_aliases_and_show_axes_through_set_display() -> None:
    session = _session(label_mode="label")
    observed = session.execute(
        TerminalAction("set_display", {"label_mode": "none", "show_axes": True})
    )

    assert observed.observation.state.display.label_mode == "dot"
    assert observed.observation.state.display.show_axes is True
    assert len(observed.screen_lines) == 14


def test_axis_footer_reports_lattice_directions_and_scale_bar() -> None:
    session = _session(label_mode="label", show_axes=True)
    footer = session.execute(TerminalAction("align", {"axis": "c"})).screen_lines[-1]

    assert footer.startswith("axes a:")
    assert "c:out" in footer or "c:in" in footer
    assert footer.rstrip().endswith(" A")
    assert "axes" in _session(label_mode="label").observe().observation.frame
    assert "axes" not in _session(label_mode="label", show_axes=False).observe().observation.frame


def test_axes_footer_reserves_a_row_instead_of_covering_the_scene() -> None:
    crystal = _crystal()
    camera = Camera.from_view_name("c", crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    plain = compose_frame(
        crystal, camera, points, depth,
        width=60, height=14, mono=True, label_mode="label_sym", show_cell=False,
    )
    framed = compose_frame(
        crystal, camera, points, depth,
        width=60, height=14, mono=True, label_mode="label_sym", show_cell=False,
        show_axes=True,
    )
    lines = framed.split("\n")

    assert len(lines) == 14
    assert lines[-1].startswith("axes a:")
    assert "O1#2" in "\n".join(lines[:-1])
    assert "O1#2" in plain


def test_repeat_title_quotes_the_displayed_supercell() -> None:
    crystal = load_for_tui(str(COD_CIF), repeat=(2, 1, 1))
    title = TerminalSession(
        crystal, width=100, height=16, charset="ascii7", display_level="atom"
    ).observe().observation.title

    assert crystal.metadata["transform_label"] == "repeat 2x1x1"
    assert f"{crystal.n_atoms} displayed" in title
    assert "repeat 2x1x1" in title
    assert "in cell" in title
    assert title.index(f"{crystal.n_atoms} displayed") < title.index("in cell")


def test_interactive_axes_stay_visible_after_the_warning_banner_mounts() -> None:
    async def exercise() -> None:
        app = CrystalTUI(_crystal(), mono=True, label_mode="dot", show_axes=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.pause()
            warning = app.query_one("#chemistry-warning")
            canvas = app.query_one("#canvas")
            assert warning.display is True
            assert "CHEMISTRY NOT AVAILABLE" in str(warning.render())
            lines = canvas.frame_text.split("\n")
            assert lines[-1].startswith("axes ")
            assert len(lines) == canvas.size.height
            assert app.controller.height == canvas.size.height

            await pilot.press("A")
            await pilot.pause()
            assert app.controller.state.display.show_axes is False
            assert not canvas.frame_text.split("\n")[-1].startswith("axes")

    asyncio.run(exercise())


def test_transform_shorthands_expand_to_canonical_specs() -> None:
    assert _transform_specs(None, None, None, None) == []
    assert _transform_specs(2, 3.5, None, None) == [
        {"kind": "repeat", "params": {"a": 2, "b": 2, "c": 2}},
        {"kind": "grow_radius", "params": {"seeds": {"all": True}, "radius": 3.5}},
    ]
    with pytest.raises(ValueError):
        _transform_specs((1, 0, 1), None, None, None)
    with pytest.raises(ValueError):
        _transform_specs(None, -1.0, None, None)


def test_load_for_tui_repeat_and_grow_radius_add_distinct_copies() -> None:
    base = load_for_tui(str(COD_CIF))
    supercell = load_for_tui(str(COD_CIF), repeat=(2, 1, 1))
    larger = load_for_tui(str(COD_CIF), repeat=(2, 2, 1))
    shell = load_for_tui(str(COD_CIF), grow_radius=2.5)

    def positions(crystal):
        return {tuple(np.round(atom.cart, 3)) for atom in crystal.atoms}

    assert len(larger.atoms) == 2 * len(supercell.atoms)
    assert len(positions(shell)) == len(shell.atoms) > len(base.atoms)
    shifts = {atom.image_shift for atom in supercell.atoms}
    assert (1, 0, 0) in shifts
