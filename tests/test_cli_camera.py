from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mat_viewer.capabilities as capability_module
from mat_viewer.agent_topology import build_topology_data, parse_polyhedron_specs
from mat_viewer.cli import _camera_spec, main
from mat_viewer.capabilities import resolve_requirements


def _structure() -> SimpleNamespace:
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
    return SimpleNamespace(frames=(SimpleNamespace(bundle=bundle),))


def _camera_args(**overrides) -> Namespace:
    values = {
        "width": 900,
        "height": 720,
        "show_hydrogen": False,
        "camera_axis": None,
        "view_direction": None,
        "camera_position": None,
        "camera_up": None,
        "camera_distance": 1.8,
        "projection": "orthographic",
    }
    values.update(overrides)
    return Namespace(**values)


def test_camera_defaults_to_orthographic_positive_c_axis() -> None:
    camera = _camera_spec(_structure(), _camera_args(), display="unit_cell")

    direction = np.asarray(camera.position) - np.asarray(camera.target)
    direction /= np.linalg.norm(direction)
    assert direction == pytest.approx([0.0, 0.0, 1.0])
    assert camera.up == pytest.approx([0.0, 1.0, 0.0])
    assert camera.projection == "orthographic"


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
