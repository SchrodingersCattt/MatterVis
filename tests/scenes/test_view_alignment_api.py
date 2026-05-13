"""Phase 4 view tools -- REST surface for axis alignment + projection.

DO NOT REMOVE -- these endpoints are documented in
``agents/dash_service.md`` (`POST /api/v2/camera/action` with
``action`` ``align`` / ``projection``) and external automation
depends on the response shape.
"""
from __future__ import annotations

import math
from pathlib import Path

from crystal_viewer.app import WORKSPACE_DIR, create_app


def _client(tmp_path: Path):
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=WORKSPACE_DIR,
    )
    return app.server.test_client()


def test_camera_action_align_returns_camera_dict(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post("/api/v2/camera/action", json={"action": "align", "axis": "c"})
    assert response.status_code == 200
    body = response.get_json()
    assert "camera" in body
    cam = body["camera"]
    for key in ("eye", "center", "up"):
        assert key in cam
        assert {"x", "y", "z"}.issubset(cam[key].keys())


def test_camera_action_align_persists_into_state(tmp_path: Path):
    client = _client(tmp_path)
    align_resp = client.post(
        "/api/v2/camera/action", json={"action": "align", "axis": "a*"}
    )
    assert align_resp.status_code == 200
    expected = align_resp.get_json()["camera"]

    state_resp = client.get("/api/v2/state")
    assert state_resp.status_code == 200
    persisted = state_resp.get_json()["camera"]
    assert math.isclose(expected["eye"]["x"], persisted["eye"]["x"], rel_tol=1e-9)


def test_camera_action_projection_round_trips(tmp_path: Path):
    client = _client(tmp_path)
    resp = client.post(
        "/api/v2/camera/action", json={"action": "projection", "type": "orthographic"}
    )
    assert resp.status_code == 200
    cam = resp.get_json()["camera"]
    assert cam.get("projection") == {"type": "orthographic"}

    state = client.get("/api/v2/state").get_json()
    assert state["projection"] == "orthographic"

    # Switch back via top-level state PATCH path.
    resp2 = client.post("/api/v2/state", json={"projection": "perspective"})
    assert resp2.status_code == 200
    state2 = client.get("/api/v2/state").get_json()
    assert state2["projection"] == "perspective"


def test_camera_action_align_then_projection_keeps_both(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/api/v2/camera/action", json={"action": "align", "axis": "c"})
    client.post(
        "/api/v2/camera/action", json={"action": "projection", "type": "orthographic"}
    )
    state = client.get("/api/v2/state").get_json()
    cam = state["camera"]
    # Camera still pointing along c (eye z >> eye x, eye y for orthogonal cells).
    assert abs(cam["eye"]["z"]) > abs(cam["eye"]["x"])
    assert abs(cam["eye"]["z"]) > abs(cam["eye"]["y"])
    assert state["projection"] == "orthographic"


def test_camera_action_unknown_axis_returns_400_json(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/camera/action", json={"action": "align", "axis": "foo"}
    )
    assert response.status_code == 400
    assert response.get_json()["type"] == "ValueError"


def test_camera_action_align_reciprocal_axes_all_supported(tmp_path: Path):
    client = _client(tmp_path)
    for axis in ("a", "b", "c", "a*", "b*", "c*"):
        response = client.post(
            "/api/v2/camera/action", json={"action": "align", "axis": axis}
        )
        assert response.status_code == 200, axis
        cam = response.get_json()["camera"]
        assert cam["eye"]["x"] != 0 or cam["eye"]["y"] != 0 or cam["eye"]["z"] != 0
