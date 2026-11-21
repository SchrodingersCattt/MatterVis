"""Flat ORTEP must accept the viewer's stored position/focal camera.

The catalog load stores that legacy mapping. Plotly scene cameras only
accept eye, center, up, and projection.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.app.camera_helpers import _plotly_scene_camera


_CIF = """data_{name}
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
_atom_site_U_iso_or_equiv
C1 C {x0} 0.50 0.50 1 0.04
C2 C 0.52 0.50 0.50 1 0.05
C3 C 0.58 0.60 0.50 1 0.04
O1 O 0.58 0.70 0.50 1 0.06
"""


def _write_cif(path: Path, name: str, x0: str) -> None:
    path.write_text(_CIF.format(name=name, x0=x0), encoding="utf-8")


def _backend(tmp_path: Path) -> ViewerBackend:
    structures = tmp_path / "structures"
    structures.mkdir()
    alpha = structures / "alpha.cif"
    beta = structures / "beta.cif"
    _write_cif(alpha, "alpha", "0.40")
    _write_cif(beta, "beta", "0.30")
    local = tmp_path / ".local"
    local.mkdir()
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    "alpha": {"cif_path": str(alpha), "title": "alpha"},
                    "beta": {"cif_path": str(beta), "title": "beta"},
                }
            }
        ),
        encoding="utf-8",
    )
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def _eye(camera: dict) -> np.ndarray:
    eye = camera["eye"]
    return np.array([float(eye["x"]), float(eye["y"]), float(eye["z"])], dtype=float)


def _assert_plotly_camera(camera: dict) -> None:
    assert set(camera) <= {"eye", "center", "up", "projection"}
    assert "position" not in camera
    assert "focal_point" not in camera
    vector = _eye(camera)
    assert np.all(np.isfinite(vector))
    assert float(np.linalg.norm(vector)) > 0.1


def _figure_camera(backend: ViewerBackend, scene_id: str) -> dict:
    state = backend.get_state(scene_id)
    figure, _topology = backend.figure_for_state(state)
    payload = figure.to_plotly_json()
    assert payload["layout"]["scene"]["dragmode"] == "orbit"
    assert payload["layout"]["images"]
    assert any(trace.get("name") == "flat-ortep-camera" for trace in payload["data"])
    camera = payload["layout"]["scene"]["camera"]
    _assert_plotly_camera(camera)
    return camera


def test_plotly_scene_camera_drops_legacy_keys() -> None:
    converted = _plotly_scene_camera(
        {
            "position": [3.0, 1.0, 8.0],
            "focal_point": [0.0, 0.0, 0.0],
            "up": [0.0, 0.0, 1.0],
            "projection": {"type": "orthographic"},
        }
    )
    assert converted is not None
    _assert_plotly_camera(converted)
    assert converted["projection"]["type"] == "orthographic"
    degenerate = _plotly_scene_camera(
        {
            "position": [1.0, 1.0, 1.0],
            "focal_point": [1.0, 1.0, 1.0],
            "up": [0.0, 1.0, 0.0],
        }
    )
    assert degenerate is not None
    assert np.allclose(_eye(degenerate), [0.0, 0.0, 1.8])


def test_flat_ortep_accepts_stored_position_camera_and_keeps_other_modes(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_ids = list(backend.scene_store.order)
        assert len(scene_ids) >= 2
        stored_eyes = []
        for scene_id in scene_ids:
            state = backend.get_state(scene_id)
            stored = state.get("camera")
            assert isinstance(stored, dict)
            assert "position" in stored and "focal_point" in stored
            backend.patch_state(
                {"material": "flat", "style": "ortep"},
                scene_id=scene_id,
                broadcast=False,
            )
            # Rendering converts the layout camera and leaves the stored
            # position/focal mapping in place for the next read.
            first = _figure_camera(backend, scene_id)
            second = _figure_camera(backend, scene_id)
            assert np.allclose(_eye(first), _eye(second))
            stored_after = backend.get_state(scene_id)["camera"]
            assert stored_after["position"] == stored["position"]
            assert stored_after["focal_point"] == stored["focal_point"]
            stored_eyes.append(_eye(first))

            for material, style in (
                ("mesh", "ball_stick"),
                ("mesh", "ortep"),
                ("mesh", "ball"),
                ("mesh", "stick"),
                ("mesh", "wireframe"),
                ("flat", "ball_stick"),
            ):
                backend.patch_state(
                    {"material": material, "style": style},
                    scene_id=scene_id,
                    broadcast=False,
                )
                figure, _topology = backend.figure_for_state(backend.get_state(scene_id))
                camera = figure.to_plotly_json()["layout"]["scene"]["camera"]
                _assert_plotly_camera(camera)

            backend.patch_state(
                {"material": "flat", "style": "ortep"},
                scene_id=scene_id,
                broadcast=False,
            )
            again = _figure_camera(backend, scene_id)
            assert np.allclose(_eye(again), stored_eyes[-1])

        assert not np.allclose(stored_eyes[0], stored_eyes[1])

        active = scene_ids[0]
        other = scene_ids[1]
        backend.set_active_scene(other, broadcast=False)
        _figure_camera(backend, other)
        backend.set_active_scene(active, broadcast=False)
        _figure_camera(backend, active)
        backend.set_active_scene(other, broadcast=False)
        _figure_camera(backend, other)

        backend.patch_state(
            {
                "material": "flat",
                "style": "ortep",
                "camera": {
                    "position": [2.0, 2.0, 2.0],
                    "focal_point": [2.0, 2.0, 2.0],
                    "up": [0.0, 1.0, 0.0],
                },
            },
            scene_id=other,
            broadcast=False,
        )
        degenerate = _figure_camera(backend, other)
        assert np.allclose(_eye(degenerate), [0.0, 0.0, 1.8])
        backend.patch_state(
            {
                "camera": {
                    "position": [8.0, 1.0, 2.0],
                    "focal_point": [0.0, 0.0, 0.0],
                    "up": [0.0, 0.0, 1.0],
                }
            },
            scene_id=other,
            broadcast=False,
        )
        moved = _figure_camera(backend, other)
        assert not np.allclose(_eye(moved), _eye(degenerate))
        _figure_camera(backend, other)
    finally:
        backend.close()


def test_plotly_scene_camera_accepts_mappings_sequences_and_partial_payloads() -> None:
    converted = _plotly_scene_camera(
        {
            "eye": {"x": 1.0, "y": 2.0, "z": 3.0},
            "center": (0.0, 0.5, -0.5),
            "up": [0.0, 0.0, 1.0],
            "projection": {"type": "orthographic"},
        }
    )
    assert converted is not None

    def xyz(value):
        if isinstance(value, dict):
            return (value["x"], value["y"], value["z"])
        return tuple(value)

    assert xyz(converted["eye"]) == (1.0, 2.0, 3.0)
    assert xyz(converted["center"]) == (0.0, 0.5, -0.5)
    assert xyz(converted["up"]) == (0.0, 0.0, 1.0)
    assert set(converted) <= {"eye", "center", "up", "projection"}
    partial = _plotly_scene_camera({"eye": {"x": 0.4, "y": 0.2, "z": 1.1}})
    assert partial is not None
    assert set(partial) == {"eye"}
    with pytest.raises(ValueError):
        _plotly_scene_camera({"up": {"x": 0.0, "y": float("inf"), "z": 1.0}})


def test_flat_ortep_keeps_the_raster_after_orbit_and_projection_change(tmp_path: Path):
    backend = _backend(tmp_path)
    try:
        scene_id = next(iter(backend.scene_store.order))
        backend.patch_state(
            {"material": "flat", "style": "ortep"},
            scene_id=scene_id,
            broadcast=False,
        )
        backend.patch_state(
            {
                "camera": {
                    "eye": {"x": 1.4, "y": 0.2, "z": 0.6},
                    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
                    "projection": {"type": "orthographic"},
                }
            },
            scene_id=scene_id,
            broadcast=False,
        )
        state = backend.get_state(scene_id)
        figure, _topology = backend.figure_for_state(state)
        payload = figure.to_plotly_json()
        scene = payload["layout"]["scene"]
        assert scene["bgcolor"] == "rgba(0,0,0,0)"
        assert payload["layout"]["images"]
        assert scene["camera"]["projection"]["type"] == "orthographic"
        _assert_plotly_camera(scene["camera"])
    finally:
        backend.close()


def test_nonfinite_position_camera_is_not_replaced() -> None:
    with pytest.raises(ValueError):
        _plotly_scene_camera(
            {
                "position": [float("nan"), 0.0, 1.0],
                "focal_point": [0.0, 0.0, 0.0],
                "up": [0.0, 1.0, 0.0],
            }
        )
