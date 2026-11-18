"""Short view decompositions must not raise IndexError.

``synthetic_ordered_octahedron.cif`` scores one coordinate row. A second
synthetic cell scores two. Both used to index ``vt[2]``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mat_viewer.app import ViewerBackend
from mat_viewer.scene import build_scene_from_cif
from mat_viewer.structure.cif_parse import parse_asu
from mat_viewer.viewpoint import auto_view_dir

ROOT = Path(__file__).resolve().parents[2]
OCTAHEDRON = ROOT / "tests" / "fixtures" / "synthetic_ordered_octahedron.cif"

# Direction chosen for the spread cell before the short-SVD guard. Three
# available axes must keep this result.
_SPREAD_VIEW = np.array([0.2538444880853779, 0.9008918702904534, -0.35207501177056233])
_SPREAD_UP = np.array([-0.9558623165512663, 0.17796990310093663, -0.23378183288589785])
_FEW_ATOM_VIEW = np.array([0.174, 0.985, 0.0])
_FEW_ATOM_UP = np.array([0.0, 0.0, 1.0])

_HEADER = """data_{name}
_cell_length_a 12
_cell_length_b 12
_cell_length_c 12
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
"""


def _write(path: Path, name: str, rows: str) -> None:
    path.write_text(_HEADER.format(name=name) + rows, encoding="utf-8")


def _view(path: Path, name: str):
    atoms, cell, matrix = parse_asu(str(path))
    return auto_view_dir(
        atoms,
        np.asarray(matrix, dtype=float).T,
        cell,
        compound_name=name,
    )


def _assert_unit_pair(view, up) -> None:
    view = np.asarray(view, dtype=float)
    up = np.asarray(up, dtype=float)
    assert view.shape == (3,) and up.shape == (3,)
    assert np.all(np.isfinite(view)) and np.all(np.isfinite(up))
    assert np.isclose(np.linalg.norm(view), 1.0)
    assert np.isclose(np.linalg.norm(up), 1.0)


def test_single_point_octahedron_and_two_component_cell_have_views(tmp_path: Path):
    two = tmp_path / "two_component.cif"
    _write(
        two,
        "two_component",
        "C1 C 0.20 0.30 0.40 1\n"
        "N1 N 0.28 0.36 0.44 1\n"
        "O1 O 0.70 0.20 0.55 1\n"
        "F1 F 0.80 0.62 0.18 1\n",
    )
    for path, name in ((OCTAHEDRON, "ordered_octahedron"), (two, "two_component")):
        view, up = _view(path, name)
        _assert_unit_pair(view, up)
        again, again_up = _view(path, name)
        assert np.allclose(view, again)
        assert np.allclose(up, again_up)
        for mode in ("formula_unit", "unit_cell", "asymmetric_unit", "cluster"):
            scene = build_scene_from_cif(
                name=name,
                cif_path=str(path),
                title=name,
                display_mode=mode,
            )
            _assert_unit_pair(scene["view_direction"], scene["up"])


def test_spread_cell_keeps_its_scored_direction(tmp_path: Path):
    path = tmp_path / "spread.cif"
    _write(
        path,
        "spread",
        "C1 C 0.20 0.20 0.20 1\n"
        "C2 C 0.40 0.22 0.25 1\n"
        "C3 C 0.28 0.45 0.30 1\n"
        "O1 O 0.55 0.40 0.48 1\n",
    )
    view, up = _view(path, "spread")
    assert np.allclose(view, _SPREAD_VIEW)
    assert np.allclose(up, _SPREAD_UP)


def test_one_atom_keeps_the_fixed_fallback(tmp_path: Path):
    path = tmp_path / "one.cif"
    _write(path, "one", "C1 C 0.25 0.25 0.25 1\n")
    view, up = _view(path, "one")
    assert np.allclose(view, _FEW_ATOM_VIEW)
    assert np.allclose(up, _FEW_ATOM_UP)


def test_catalog_viewer_opens_the_single_point_structure(tmp_path: Path):
    spread = tmp_path / "spread.cif"
    _write(
        spread,
        "spread",
        "C1 C 0.20 0.20 0.20 1\n"
        "C2 C 0.40 0.22 0.25 1\n"
        "C3 C 0.28 0.45 0.30 1\n"
        "O1 O 0.55 0.40 0.48 1\n",
    )
    local = tmp_path / ".local"
    local.mkdir()
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    "ordered_octahedron": {
                        "cif_path": str(OCTAHEDRON),
                        "title": "ordered_octahedron",
                    },
                    "spread": {"cif_path": str(spread), "title": "spread"},
                }
            }
        ),
        encoding="utf-8",
    )
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        assert len(backend.scene_store.order) >= 2
        eyes = []
        for scene_id in backend.scene_store.order:
            for material, style in (
                ("mesh", "ball_stick"),
                ("mesh", "ball"),
                ("flat", "ball_stick"),
            ):
                backend.patch_state(
                    {"material": material, "style": style},
                    scene_id=scene_id,
                    broadcast=False,
                )
                figure, _topology = backend.figure_for_state(backend.get_state(scene_id))
                camera = figure.to_plotly_json()["layout"]["scene"]["camera"]
                eye = np.array(
                    [camera["eye"]["x"], camera["eye"]["y"], camera["eye"]["z"]],
                    dtype=float,
                )
                assert np.all(np.isfinite(eye))
            eyes.append(eye)
            backend.set_active_scene(scene_id, broadcast=False)
            figure, _topology = backend.figure_for_state(backend.get_state(scene_id))
            assert figure.to_plotly_json()["layout"]["scene"]["camera"]["eye"]
        assert not np.allclose(eyes[0], eyes[1])
    finally:
        backend.close()
