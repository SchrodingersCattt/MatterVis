from __future__ import annotations

from crystal_viewer.app import create_app


def test_healthz_v2_returns_lightweight_liveness_payload():
    app = create_app()
    server = app.server
    server.config["TESTING"] = True
    client = server.test_client()

    response = client.get("/api/v2/healthz")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["uptime_s"] >= 0
    assert isinstance(body["server_started_at"], str)
    assert body["scenes"] >= 1
    assert body["structures"] >= 1


def test_state_echoes_server_started_at_for_restart_detection():
    app = create_app()
    server = app.server
    server.config["TESTING"] = True
    client = server.test_client()

    response = client.get("/api/v2/state")

    assert response.status_code == 200
    assert "server_started_at" in response.get_json()


def test_screenshot_accepts_size_fast_and_version_query(monkeypatch):
    from crystal_viewer import app as app_module

    calls = []

    def fake_to_image(_fig, **kwargs):
        calls.append(kwargs)
        return b"png-bytes"

    monkeypatch.setattr(app_module.pio, "to_image", fake_to_image)
    app = create_app()
    server = app.server
    server.config["TESTING"] = True
    client = server.test_client()

    version = client.post("/api/v2/state", json={"atom_scale": 1.05}).get_json()["version"]
    response = client.get(f"/api/v2/screenshot?width=320&height=240&scale=1&fast=true&at_version={version}")

    assert response.status_code == 200
    assert response.data == b"png-bytes"
    assert calls[-1]["width"] == 320
    assert calls[-1]["height"] == 240
    assert calls[-1]["scale"] == 1.0
