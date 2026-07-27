from __future__ import annotations

import asyncio
from dataclasses import replace
import io
from pathlib import Path
from contextlib import redirect_stdout

import numpy as np
import pytest

from crystal_viewer.cli import main
from crystal_viewer.math.camera import Camera, ProjectionMode, project_points
from crystal_viewer.tui.app import CrystalTUI
from crystal_viewer.tui.compositor import (
    DISPLAY_LEVELS,
    _compute_viewport,
    compose_frame,
    resolve_label_mode,
    resolve_molecule_detail,
)
from crystal_viewer.tui.crystal_ir import AtomIR, CrystalIR
from crystal_viewer.tui.loader_adapter import load_for_tui
from crystal_viewer.tui import run_tui
from crystal_viewer.tui.summary import build_scope_summary


ROOT = Path(__file__).resolve().parents[2]
DAP4 = ROOT / "scripts" / "data" / "DAP-4.cif"
DISORDER_CIF = """data_disorder
_cell_length_a 20
_cell_length_b 20
_cell_length_c 20
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x,y,z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
_atom_site_disorder_assembly
_atom_site_disorder_group
C1A C 0.25 0.25 0.25 0.70 A 1
C1B C 0.25 0.25 0.25 0.30 A 2
N1 N 0.75 0.75 0.75 1.00 . .
"""


def _small_crystal() -> CrystalIR:
    atoms = [
        AtomIR(
            element="C",
            cart=np.array([-1.0, 0.0, 0.0]),
            frac=np.array([0.0, 0.0, 0.0]),
            label="C1",
            index=0,
        ),
        AtomIR(
            element="O",
            cart=np.array([1.0, 0.0, 0.0]),
            frac=np.array([0.0, 0.0, 0.0]),
            label="O1",
            index=1,
        ),
    ]
    return CrystalIR(title="small", formula="CO", atoms=atoms)


def test_canonical_cif_display_modes_and_bonds() -> None:
    automatic = load_for_tui(str(DAP4))
    formula = load_for_tui(str(DAP4), display_mode="formula_unit")
    asymmetric = load_for_tui(str(DAP4), display_mode="asymmetric_unit")

    assert automatic.n_atoms == 581
    assert automatic.metadata["display_mode"] == "unit_cell"
    assert automatic.source_site_atom_count == 336
    assert automatic.expanded_atom_count == 336
    assert automatic.canonical_formula == "C48H144Cl24N24O96"
    assert automatic.metadata["display_atom_count"] == 581
    assert len({atom.display_copy_id for atom in automatic.atoms}) == 581
    display_ids = {atom.display_copy_id for atom in automatic.atoms}
    assert all(bond.start_display_copy_id in display_ids for bond in automatic.bonds)
    assert all(bond.end_display_copy_id in display_ids for bond in automatic.bonds)
    assert formula.n_atoms == 42
    assert formula.element_counts() == {"C": 6, "H": 18, "N": 3, "Cl": 3, "O": 12}
    assert formula.per_formula_unit == {
        "C6H14N2_1": 1,
        "ClO4_1": 3,
        "H4N_1": 1,
    }
    assert asymmetric.n_atoms == 336
    assert formula.bonds
    for bond in formula.bonds:
        assert bond.start is not None
        assert bond.end is not None
        assert bond.distance == pytest.approx(
            np.linalg.norm(bond.end - bond.start), abs=1e-12
        )
        assert bond.distance < 3.5


def test_displayed_boundary_molecules_have_unique_groups() -> None:
    unit_cell = load_for_tui(str(DAP4), display_mode="unit_cell")
    molecule_members: dict[int, list[np.ndarray]] = {}
    for atom in unit_cell.atoms:
        if atom.molecule_index >= 0:
            molecule_members.setdefault(atom.molecule_index, []).append(atom.cart)

    expected_groups = sum(len(indices) for indices in unit_cell.species_map.values())
    assert len(molecule_members) == expected_groups
    assert sum(len(coords) for coords in molecule_members.values()) == sum(
        atom.molecule_index >= 0 for atom in unit_cell.atoms
    )
    assert max(
        np.ptp(np.asarray(coords), axis=0).max()
        for coords in molecule_members.values()
    ) < 5.0
    assert all("H" in species or species == "ClO4_1" for species in unit_cell.species_map)


def test_viewport_zoom_works_in_both_directions() -> None:
    points = np.array([[-1.0, -1.0], [1.0, 1.0]])
    zoomed_out = _compute_viewport(points, [], 80, 24, zoom=0.5)
    normal = _compute_viewport(points, [], 80, 24, zoom=1.0)
    zoomed_in = _compute_viewport(points, [], 80, 24, zoom=2.0)

    out_span = zoomed_out.x_max - zoomed_out.x_min
    normal_span = normal.x_max - normal.x_min
    in_span = zoomed_in.x_max - zoomed_in.x_min
    assert out_span > normal_span > in_span

    with pytest.raises(ValueError, match="zoom must be greater than zero"):
        _compute_viewport(points, [], 80, 24, zoom=0.0)


def test_adaptive_labels_make_crowded_views_readable() -> None:
    assert resolve_label_mode(
        "auto", atom_count=336, width=80, height=24, zoom=1.0
    ) == "dot"
    assert resolve_label_mode(
        "auto", atom_count=42, width=80, height=24, zoom=1.0
    ) == "element"
    assert resolve_label_mode(
        "label", atom_count=336, width=80, height=24, zoom=1.0
    ) == "label"


def test_unit_cell_molecule_view_labels_each_species_once() -> None:
    crystal = load_for_tui(str(DAP4))
    camera = Camera.from_view_name("diagonal", crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    frame = compose_frame(
        crystal,
        camera,
        points,
        depth,
        width=100,
        height=26,
        mono=True,
        display_level="molecule",
    )
    assert frame.count("C6H14N2") == 1
    assert frame.count("ClO4") == 1
    assert frame.count("H4N") == 1
    assert resolve_molecule_detail(
        molecule_count=sum(len(v) for v in crystal.species_map.values()),
        width=100,
        height=26,
    ) == "centroid"


@pytest.mark.parametrize("width,height", [(12, 6), (20, 8), (40, 12)])
def test_frame_respects_requested_terminal_size(width: int, height: int) -> None:
    crystal = _small_crystal()
    camera = Camera.from_view_name("diagonal", crystal)
    points, depth = project_points(camera, crystal.cart_coords)

    frame = compose_frame(
        crystal,
        camera,
        points,
        depth,
        width=width,
        height=height,
        mono=True,
        label_mode="dot",
        show_cell=False,
    )
    lines = frame.splitlines()
    assert len(lines) <= height
    assert max((len(line) for line in lines), default=0) <= width


def test_cli_honors_projection_and_explicit_camera(capsys) -> None:
    main([
        "tui",
        str(DAP4),
        "--no-interaction",
        "--format",
        "structured",
        "--display",
        "formula_unit",
        "--projection",
        "perspective",
        "--azimuth",
        "45",
        "--elevation",
        "15",
        "--roll",
        "10",
        "--zoom",
        "1.3",
    ])
    output = capsys.readouterr().out
    assert "  n_atoms: 42\n" in output
    assert "  projection: perspective\n" in output
    assert "  azimuth: 45.0\n" in output
    assert "  elevation: 15.0\n" in output


@pytest.mark.parametrize(
    "args,error",
    [
        (["--zoom", "0"], "--zoom must be greater than zero"),
        (["--width", "0"], "--width must be greater than zero"),
        (["--height", "0"], "--height must be greater than zero"),
    ],
)
def test_cli_rejects_invalid_viewport_values(args, error, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["tui", str(DAP4), "--no-interaction", *args])
    assert exc.value.code == 2
    assert error in capsys.readouterr().err


def test_textual_app_preserves_prepared_camera_and_reset() -> None:
    crystal = _small_crystal()
    initial = replace(
        Camera.from_view_name("c", crystal),
        projection=ProjectionMode.PERSPECTIVE,
        viewport_zoom=1.7,
        roll=12.0,
    )

    async def exercise() -> None:
        app = CrystalTUI(crystal, mono=True, camera=initial)
        async with app.run_test(size=(40, 12)) as pilot:
            await pilot.pause()
            assert "2 displayed" in app.sub_title
            assert "label" in app.sub_title
            assert app.camera.projection is ProjectionMode.PERSPECTIVE
            assert app.camera.viewport_zoom == pytest.approx(1.7)
            await pilot.press("e", "+", "r")
            await pilot.pause()
            assert app.camera.azimuth == pytest.approx(initial.azimuth)
            assert app.camera.viewport_zoom == pytest.approx(initial.viewport_zoom)
            assert app.camera.roll == pytest.approx(initial.roll)

    asyncio.run(exercise())


def test_only_canonical_display_levels_are_exposed() -> None:
    assert DISPLAY_LEVELS == ("atom", "molecule")


def test_textual_can_start_in_readable_molecule_level(monkeypatch) -> None:
    crystal = _small_crystal()
    app = CrystalTUI(crystal, mono=True, initial_level="molecule")
    assert app._display_level == "molecule"

    captured = {}

    class FakeApp:
        def __init__(self, *, crystal, initial_level, **kwargs):
            captured["crystal"] = crystal
            captured["initial_level"] = initial_level

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr("crystal_viewer.tui.app.CrystalTUI", FakeApp)
    main(["tui", str(DAP4)])
    assert captured["ran"] is True
    assert captured["crystal"].n_atoms == 581
    assert captured["crystal"].metadata["display_mode"] == "unit_cell"
    assert captured["initial_level"] == "molecule"


def test_minor_atoms_remain_available_for_interactive_toggle(tmp_path) -> None:
    path = tmp_path / "disorder.cif"
    path.write_text(DISORDER_CIF, encoding="utf-8")
    crystal = load_for_tui(str(path), display_mode="unit_cell")
    assert {atom.label for atom in crystal.atoms if atom.is_minor} == {"C1B"}

    camera = Camera.from_view_name("diagonal", crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    hidden = compose_frame(
        crystal, camera, points, depth,
        width=40, height=12, mono=True, label_mode="label",
        show_cell=False, show_minor=False,
    )
    shown = compose_frame(
        crystal, camera, points, depth,
        width=40, height=12, mono=True, label_mode="label",
        show_cell=False, show_minor=True,
    )
    assert "C1B" not in hidden
    assert "C1B*" in shown

    hidden_summary = build_scope_summary(crystal, show_minor=False)
    shown_summary = build_scope_summary(crystal, show_minor=True)
    assert hidden_summary["display_atom_count"] == 3
    assert hidden_summary["visible_atom_count"] == 2
    assert hidden_summary["visible_formula"] == "CN"
    assert shown_summary["visible_atom_count"] == 3
    assert shown_summary["visible_formula"] == "C2N"


def test_minor_toggle_refreshes_scoped_title(tmp_path) -> None:
    path = tmp_path / "disorder.cif"
    path.write_text(DISORDER_CIF, encoding="utf-8")
    crystal = load_for_tui(str(path), display_mode="unit_cell")

    async def exercise() -> None:
        app = CrystalTUI(crystal, mono=True, show_minor=False)
        async with app.run_test(size=(60, 16)) as pilot:
            await pilot.pause()
            assert "3 displayed/2 visible" in app.sub_title
            await pilot.press("n")
            await pilot.pause()
            assert "3 displayed" in app.sub_title
            assert "/2 visible" not in app.sub_title

    asyncio.run(exercise())


def test_narrow_frames_truncate_long_labels() -> None:
    crystal = _small_crystal()
    crystal.atoms[0].label = "VeryLongAtomLabel"
    camera = Camera.from_view_name("diagonal", crystal)
    points, depth = project_points(camera, crystal.cart_coords)
    frame = compose_frame(
        crystal, camera, points, depth,
        width=1, height=4, mono=True, label_mode="label", show_cell=False,
    )
    assert max((len(line) for line in frame.splitlines()), default=0) <= 1


def test_structured_and_public_helper_share_minor_visibility(tmp_path) -> None:
    path = tmp_path / "disorder.cif"
    path.write_text(DISORDER_CIF, encoding="utf-8")

    hidden = io.StringIO()
    with redirect_stdout(hidden):
        run_tui(
            str(path),
            interactive=False,
            format="structured",
            projection="perspective",
            show_minor=False,
        )
    shown = io.StringIO()
    with redirect_stdout(shown):
        run_tui(
            str(path),
            interactive=False,
            format="structured",
            projection="perspective",
            show_minor=True,
        )

    assert "  projection: perspective\n" in hidden.getvalue()
    assert "  - label: C1B\n" not in hidden.getvalue()
    assert "  - label: C1B\n" in shown.getvalue()
    assert "  n_atoms: 2\n" in hidden.getvalue()
    assert "  n_atoms: 3\n" in shown.getvalue()
    assert "  formula: CN\n" in hidden.getvalue()
    assert "  formula: C2N\n" in shown.getvalue()
    assert "  display_mode: unit_cell\n" in hidden.getvalue()
    assert "  display_atom_count: 3\n" in hidden.getvalue()
    assert "  visible_atom_count: 2\n" in hidden.getvalue()
    assert "  canonical_formula: C2N\n" in hidden.getvalue()
    assert "  display_formula: C2N\n" in hidden.getvalue()
    assert "  visible_formula: CN\n" in hidden.getvalue()
    assert "canonical_composition:\n  C: 2\n  N: 1\n" in hidden.getvalue()
    assert "display_composition:\n  C: 2\n  N: 1\n" in hidden.getvalue()
    assert "visible_composition:\n  C: 1\n  N: 1\n" in hidden.getvalue()
    assert "composition:\n  C: 1\n  N: 1\n" in hidden.getvalue()


def test_non_cif_ir_has_deterministic_identity_and_scopes(tmp_path) -> None:
    path = tmp_path / "pair.xyz"
    path.write_text("2\npair\nC 0 0 0\nO 1.2 0 0\n", encoding="utf-8")
    crystal = load_for_tui(str(path))
    assert crystal.source_site_atom_count == 2
    assert crystal.expanded_atom_count == 2
    assert crystal.canonical_formula == "CO"
    assert crystal.metadata["display_atom_count"] == 2
    assert [atom.source_index for atom in crystal.atoms] == [0, 1]
    assert len({atom.display_copy_id for atom in crystal.atoms}) == 2


def test_filtered_molecule_summary_drops_removed_groups() -> None:
    from crystal_viewer.cli import _filter_crystal

    crystal = load_for_tui(str(DAP4), display_mode="formula_unit")
    first_molecule = next(atom.molecule_index for atom in crystal.atoms if atom.molecule_index >= 0)
    keep = {
        index
        for index, atom in enumerate(crystal.atoms)
        if atom.molecule_index == first_molecule
    }
    filtered = _filter_crystal(crystal, keep)
    summary = build_scope_summary(filtered, show_minor=True, display_level="molecule")
    assert filtered.n_molecules == 1
    assert sum(len(indices) for indices in filtered.species_map.values()) == 1
    assert summary["visible_marker_count"] == 1
    assert filtered.metadata["display_atom_count"] == filtered.n_atoms
