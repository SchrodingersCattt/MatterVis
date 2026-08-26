from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mat_viewer.capabilities as capability_module
from mat_viewer.agent_topology import build_topology_data, parse_polyhedron_specs
from mat_viewer.cli import (
    _camera_spec,
    _display_mode,
    _effective_show_cell,
    _inspect_payload,
    main,
)
from mat_viewer.capabilities import resolve_requirements


def _structure(scene_overrides=None) -> SimpleNamespace:
    bundle = SimpleNamespace(
        M=np.diag([3.0, 4.0, 5.0]),
        scene={
            "bounds": {
                "center": [0.0, 0.0, 0.0],
                "mins": [-1.0, -1.0, -1.0],
                "maxs": [1.0, 1.0, 1.0],
            }
        },
    )
    if scene_overrides:
        bundle.scene.update(scene_overrides)
    return SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))


def _camera_args(**overrides) -> Namespace:
    values = {
        "output": "figure.png",
        "width": 900,
        "height": 720,
        "show_hydrogen": False,
        "show_unit_cell": None,
        "camera_axis": None,
        "view_direction": None,
        "camera_position": None,
        "camera_up": None,
        "camera_distance": 1.8,
        "projection": "orthographic",
    }
    values.update(overrides)
    return Namespace(**values)


def test_animation_camera_fits_all_selected_frames() -> None:
    first = _structure(
        {
            "bounds": {
                "center": [0.0, 0.0, 0.0],
                "mins": [-1.0, -1.0, -0.1],
                "maxs": [1.0, 1.0, 0.1],
            }
        }
    ).frames[0]
    widest = _structure(
        {
            "bounds": {
                "center": [0.0, 0.0, 0.0],
                "mins": [-1.0, -4.0, -0.1],
                "maxs": [1.0, 4.0, 0.1],
            }
        }
    ).frames[0]
    structure = SimpleNamespace(frames=(first, widest))

    animation = _camera_spec(
        structure,
        _camera_args(output="movie.gif", show_unit_cell=False),
        display="formula_unit",
    )
    static = _camera_spec(
        structure,
        _camera_args(output="figure.png", show_unit_cell=False),
        display="formula_unit",
    )

    assert animation.target == pytest.approx([0.0, 0.0, 0.0])
    assert animation.ortho_scale == pytest.approx(4.48)
    assert static.ortho_scale == pytest.approx(1.12)


def test_camera_defaults_to_orthographic_positive_c_axis() -> None:
    structure = _structure()
    matrix_before = structure.frames[0].bundle.M.copy()
    camera = _camera_spec(structure, _camera_args(), display="unit_cell")

    direction = np.asarray(camera.position) - np.asarray(camera.target)
    direction /= np.linalg.norm(direction)
    assert direction == pytest.approx([0.0, 0.0, 1.0])
    assert camera.up == pytest.approx([0.0, 1.0, 0.0])
    assert camera.projection == "orthographic"
    assert structure.frames[0].bundle.M == pytest.approx(matrix_before)


def test_unit_cell_camera_targets_cell_center_with_asymmetric_context() -> None:
    structure = _structure(
        {
            "bounds": {
                "center": [-0.5, 0.0, 0.0],
                "mins": [-2.0, -1.0, -1.0],
                "maxs": [1.0, 1.0, 1.0],
            },
            "has_lattice": True,
            "pbc": [True, True, True],
            "synthetic_cell": False,
        }
    )

    camera = _camera_spec(structure, _camera_args(), display="unit_cell")

    assert camera.target == pytest.approx([1.5, 2.0, 2.5])


def test_auto_display_uses_periodic_unit_cell_context() -> None:
    args = Namespace(view="auto")
    periodic = _structure(
        {"has_lattice": True, "pbc": [True, True, True], "synthetic_cell": False}
    )
    nonperiodic = _structure(
        {"has_lattice": False, "pbc": [False, False, False], "synthetic_cell": True}
    )

    assert _display_mode(periodic, args) == "unit_cell"
    assert _display_mode(nonperiodic, args) == "cluster"
    assert _effective_show_cell(periodic, Namespace(show_unit_cell=None)) is True
    assert _effective_show_cell(nonperiodic, Namespace(show_unit_cell=None)) is False
    assert _effective_show_cell(nonperiodic, Namespace(show_unit_cell=True)) is True
    assert _display_mode(nonperiodic, Namespace(view="formula_unit")) == "formula_unit"


def test_camera_fit_includes_the_visible_unit_cell() -> None:
    structure = _structure()
    camera = _camera_spec(
        structure, _camera_args(show_unit_cell=True), display="formula_unit"
    )

    assert camera.target == pytest.approx([1.0, 1.5, 2.0])
    assert camera.ortho_scale == pytest.approx(2.8)

    atoms_only = _camera_spec(
        structure,
        _camera_args(show_unit_cell=False),
        display="formula_unit",
    )
    assert atoms_only.target == pytest.approx([0.0, 0.0, 0.0])


def test_inspect_reports_disorder_from_mck_site_records(tmp_path: Path) -> None:
    source = tmp_path / "disorder.cif"
    source.write_text("data_disorder\n", encoding="utf-8")
    sites = [
        SimpleNamespace(occupancy=0.5, disorder_group=0),
        SimpleNamespace(occupancy=1.0, disorder_group=0),
    ]
    crystal = SimpleNamespace(
        get_site_records=lambda: sites,
        get_bond_records=lambda: [],
    )
    bundle = SimpleNamespace(
        molcrys_analysis=SimpleNamespace(crystal=crystal),
        raw_atoms=[object(), object()],
        scene={
            "draw_atoms": [],
            "has_lattice": False,
            "pbc": [False, False, False],
            "synthetic_cell": True,
        },
        fragment_table=[],
        metadata=lambda: {"has_minor": False, "warnings": []},
    )
    structure = SimpleNamespace(
        path=source,
        input_format="cif",
        total_frames=1,
        frames=(SimpleNamespace(index=0, bundle=bundle),),
    )

    payload = _inspect_payload(structure)

    assert payload["structure"]["has_disorder"] is True
    assert payload["structure"]["periodic"] is False
    assert payload["structure"]["has_lattice"] is False
    assert payload["structure"]["synthetic_cell"] is True
    assert payload["structure"]["pbc"] == [False, False, False]
    assert payload["warnings"] == ["MolCrysKit reports disorder in 1 of 2 sites."]


def test_explicit_camera_position_is_absolute_cartesian() -> None:
    camera = _camera_spec(
        _structure(),
        _camera_args(
            camera_position=[12.0, 1.0, 2.0],
            camera_up=[0.0, 0.0, 1.0],
        ),
        display="unit_cell",
    )

    assert camera.position == pytest.approx([12.0, 1.0, 2.0])
    assert camera.up == pytest.approx([0.0, 0.0, 1.0])


def test_zero_view_direction_is_rejected() -> None:
    with pytest.raises(ValueError, match="direction must be non-zero"):
        _camera_spec(
            _structure(),
            _camera_args(view_direction=[0.0, 0.0, 0.0]),
            display="unit_cell",
        )


def test_plotly_static_preflight_uses_capability_registry() -> None:
    resolution = resolve_requirements("plotly-export")

    assert resolution.extras == ("plotly-export",)
    assert resolution.install_command == (
        'python -m pip install "matter-vis[plotly-export]"'
    )


def test_polyhedron_json_supports_atom_and_molecule_centres() -> None:
    specs = parse_polyhedron_specs(
        [
            '{"center":"Pb","ligand":"I","level":"atom","fallback_max":6}',
            '{"center":"C6N2","ligand":"ClO4","level":"molecule",'
            '"center_kind":"heavy_centroid","hard_cutoff":8.0}',
        ]
    )

    assert specs[0]["center_species"] == "Pb"
    assert specs[0]["ligand_species"] == "I"
    assert specs[0]["level"] == "atom"
    assert specs[0]["fallback_max"] == 6
    assert specs[1]["center_kind"] == "heavy_centroid"
    assert specs[1]["hard_cutoff"] == 8.0


def test_polyhedron_json_rejects_removed_paint_properties() -> None:
    with pytest.raises(ValueError, match="edge_width, flatshading"):
        parse_polyhedron_specs(
            ['{"center":"Pb","ligand":"I","edge_width":2.5,"flatshading":false}']
        )


def test_backend_neutral_topology_preserves_spec_paint(monkeypatch) -> None:
    import mat_viewer.topology as topology_module

    fragment = {
        "index": 0,
        "formula": "C6N2",
        "species": "C6N2",
        "elem_set": ["C", "N"],
        "center": [0.0, 0.0, 0.0],
    }
    bundle = SimpleNamespace(
        scene={"fragment_table": [fragment]},
        fragment_table=[fragment],
        topology_fragment_table=[fragment],
    )
    structure = SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))
    monkeypatch.setattr(
        topology_module,
        "analyze_topology",
        lambda *args, **kwargs: {
            "center_coords": [0.0, 0.0, 0.0],
            "center_label": "C6N2",
            "shell_coords": [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
                [-1.0, -1.0, -1.0],
            ],
            "hull": {"simplices": [[0, 1, 2], [0, 1, 3]]},
        },
    )

    result = build_topology_data(
        structure,
        [
            '{"id":"shell","center":"C6N2","ligand":"ClO4",'
            '"color":"#0072B2","opacity":0.72,"edge_opacity":0.65}'
        ],
    )

    spec = result["spec_results"][0]
    assert spec["color"] == "#0072B2"
    assert spec["opacity"] == 0.72
    assert spec["edge_opacity"] == 0.65


def test_polyhedron_check_uses_base_capability_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        capability_module.CapabilitySpec, "available", lambda self: True
    )
    output = tmp_path / "not-created.svg"

    main(
        [
            "render",
            str(tmp_path / "not-loaded.cif"),
            "-o",
            str(output),
            "--backend",
            "cpu",
            "--polyhedron",
            '{"center":"Pb","ligand":"I","level":"atom"}',
            "--check",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["requirements"]["extras"] == []
    assert not output.exists()


def test_cpu_check_accepts_explicit_lattice_axes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        capability_module.CapabilitySpec, "available", lambda self: True
    )

    main(
        [
            "render",
            str(tmp_path / "not-loaded.cif"),
            "-o",
            str(tmp_path / "not-created.png"),
            "--backend",
            "cpu",
            "--show-axes",
            "--check",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_check_exposes_camera_and_mesh_quality_cli_parity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        capability_module.CapabilitySpec, "available", lambda self: True
    )

    main(
        [
            "render",
            str(tmp_path / "not-loaded.cif"),
            "-o",
            str(tmp_path / "not-created.png"),
            "--backend",
            "cpu",
            "--perspective",
            "--camera-target",
            "1",
            "2",
            "3",
            "--field-of-view",
            "37",
            "--camera-clip",
            "0.2",
            "400",
            "--sphere-detail",
            "18",
            "30",
            "--cylinder-sides",
            "20",
            "--check",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["camera"]["target"] == [1.0, 2.0, 3.0]
    assert payload["camera"]["field_of_view"] == 37.0
    assert payload["camera"]["clip"] == [0.2, 400.0]
    assert payload["render"]["sphere_detail"] == [18, 30]
    assert payload["render"]["cylinder_sides"] == 20
