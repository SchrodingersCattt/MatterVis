"""Phase 4 view tools -- VESTA-style axis alignment math.

Covers ``camera_for_axis`` (the pure math) plus the
``ViewerBackend.align_camera`` / ``set_projection`` /
``camera_action`` wrappers. The REST surface is exercised in
``tests/scenes/test_view_alignment_api.py``.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from crystal_viewer.app import (
    WORKSPACE_DIR,
    ViewerBackend,
    _AXIS_VIEW_KEYS,
    _coerce_projection,
    _normalize_axis_key,
    camera_for_axis,
)


def _backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=WORKSPACE_DIR,
    )


# ---- pure-math layer ---------------------------------------------------


def test_camera_for_axis_orthogonal_lattice_along_c():
    M = np.diag([3.0, 4.0, 5.0])
    cam = camera_for_axis(M, "c", eye_distance=2.0)
    eye = np.array([cam["eye"][k] for k in "xyz"])
    up = np.array([cam["up"][k] for k in "xyz"])

    assert np.allclose(eye, [0.0, 0.0, 2.0])
    # Convention: looking down c, up is +b. For an orthogonal cell that
    # is exactly the world y-axis; for non-orthogonal cells the test
    # below verifies orthogonality only.
    assert np.allclose(up, [0.0, 1.0, 0.0])


def test_camera_for_axis_orthogonal_lattice_along_a():
    M = np.diag([3.0, 4.0, 5.0])
    cam = camera_for_axis(M, "a", eye_distance=1.5)
    eye = np.array([cam["eye"][k] for k in "xyz"])
    up = np.array([cam["up"][k] for k in "xyz"])

    assert np.allclose(eye, [1.5, 0.0, 0.0])
    # Looking down a, up = c-projected = +z.
    assert np.allclose(up, [0.0, 0.0, 1.0])


def test_camera_for_axis_oblique_cell_keeps_up_perpendicular():
    # Triclinic-ish: a along x, b in xy, c with non-trivial xz tilt.
    M = np.array(
        [
            [3.0, 0.6, 0.4],
            [0.0, 4.0, 0.2],
            [0.0, 0.0, 5.0],
        ]
    )
    for axis in _AXIS_VIEW_KEYS:
        cam = camera_for_axis(M, axis, eye_distance=2.0)
        eye = np.array([cam["eye"][k] for k in "xyz"])
        up = np.array([cam["up"][k] for k in "xyz"])

        # ``up`` must be unit length and orthogonal to the view direction.
        assert math.isclose(np.linalg.norm(up), 1.0, rel_tol=1e-6)
        view_dir = eye / np.linalg.norm(eye)
        assert abs(np.dot(view_dir, up)) < 1e-9, axis


def test_camera_for_axis_reciprocal_uses_inverse_columns():
    # Build a non-trivial cell so a* != a / |a|^2 trivially.
    M = np.array(
        [
            [4.0, 1.0, 0.0],
            [0.0, 3.0, 0.5],
            [0.0, 0.0, 5.0],
        ]
    )
    cam = camera_for_axis(M, "a*", eye_distance=1.0)
    eye = np.array([cam["eye"][k] for k in "xyz"])

    expected_dir = np.linalg.inv(M)[:, 0]
    expected_dir /= np.linalg.norm(expected_dir)
    assert np.allclose(eye, expected_dir, atol=1e-8)


def test_camera_for_axis_eye_distance_is_preserved():
    M = np.diag([2.5, 2.5, 2.5])
    cam = camera_for_axis(M, "b", eye_distance=4.2)
    eye = np.array([cam["eye"][k] for k in "xyz"])
    assert math.isclose(np.linalg.norm(eye), 4.2, rel_tol=1e-9)


def test_camera_for_axis_attaches_projection_kwarg():
    M = np.eye(3)
    cam = camera_for_axis(M, "c", projection="orthographic")
    assert cam.get("projection") == {"type": "orthographic"}

    cam2 = camera_for_axis(M, "c")
    assert "projection" not in cam2


def test_camera_for_axis_rejects_unknown_axis():
    with pytest.raises(ValueError):
        camera_for_axis(np.eye(3), "d")


def test_normalize_axis_key_accepts_aliases():
    assert _normalize_axis_key("A*") == "a*"
    assert _normalize_axis_key("a-star") == "a*"
    assert _normalize_axis_key("c reciprocal") == "c*"  # whitespace stripped
    assert _normalize_axis_key("c_reciprocal") == "c*"
    assert _normalize_axis_key("foo") is None


def test_coerce_projection_clamps_to_known_values():
    assert _coerce_projection("perspective") == "perspective"
    assert _coerce_projection("orthographic") == "orthographic"
    assert _coerce_projection("garbage") == "perspective"
    assert _coerce_projection("garbage", fallback="orthographic") == "orthographic"


# ---- backend wrappers --------------------------------------------------


def test_align_camera_writes_state_and_preserves_eye_distance(tmp_path: Path):
    backend = _backend(tmp_path)
    initial_eye = np.array(
        [
            backend.get_camera()["eye"]["x"],
            backend.get_camera()["eye"]["y"],
            backend.get_camera()["eye"]["z"],
        ]
    )
    initial_distance = float(np.linalg.norm(initial_eye))

    camera = backend.align_camera("c")
    new_eye = np.array([camera["eye"][k] for k in "xyz"])
    new_distance = float(np.linalg.norm(new_eye))

    assert math.isclose(new_distance, initial_distance, rel_tol=1e-6)
    persisted = backend.get_state().get("camera")
    assert persisted is not None
    assert math.isclose(persisted["eye"]["x"], new_eye[0], rel_tol=1e-9)


def test_align_camera_via_camera_action(tmp_path: Path):
    backend = _backend(tmp_path)
    camera = backend.camera_action("align", axis="a*")
    eye = np.array([camera["eye"][k] for k in "xyz"])
    assert eye.dot(eye) > 1e-6


def test_align_camera_rejects_unknown_axis(tmp_path: Path):
    backend = _backend(tmp_path)
    with pytest.raises(ValueError):
        backend.align_camera("d")


def test_set_projection_round_trips(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.set_projection("orthographic")
    assert backend.get_state()["projection"] == "orthographic"
    cam = backend.get_camera()
    assert cam.get("projection") == {"type": "orthographic"}

    backend.set_projection("perspective")
    assert backend.get_state()["projection"] == "perspective"


def test_set_projection_via_camera_action(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.camera_action("projection", type="orthographic")
    assert backend.get_state()["projection"] == "orthographic"
    backend.camera_action("set_projection", type="perspective")
    assert backend.get_state()["projection"] == "perspective"


def test_set_projection_clamps_unknown_values(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.set_projection("perspective")
    backend.set_projection("garbage")  # should silently fall back, not raise
    assert backend.get_state()["projection"] == "perspective"


def test_align_preserves_projection(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.set_projection("orthographic")
    cam = backend.align_camera("c")
    assert cam.get("projection") == {"type": "orthographic"}


def test_orbit_preserves_projection(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.set_projection("orthographic")
    cam = backend.camera_action("orbit", yaw_deg=15.0, pitch_deg=0.0)
    assert cam.get("projection") == {"type": "orthographic"}


def test_normalize_state_accepts_projection_patch(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.patch_state({"projection": "orthographic"})
    assert backend.get_state()["projection"] == "orthographic"
    backend.patch_state({"projection": "garbage"})  # falls back to current
    assert backend.get_state()["projection"] == "orthographic"


def test_normalize_state_picks_up_projection_from_camera_dict(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.patch_state(
        {
            "camera": {
                "eye": {"x": 1.0, "y": 1.0, "z": 1.0},
                "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                "up": {"x": 0.0, "y": 0.0, "z": 1.0},
                "projection": {"type": "orthographic"},
            }
        }
    )
    assert backend.get_state()["projection"] == "orthographic"


def test_style_for_state_reflects_projection(tmp_path: Path):
    backend = _backend(tmp_path)
    backend.set_projection("orthographic")
    style = backend.style_for_state(backend.get_state())
    assert style.get("projection") == "orthographic"
