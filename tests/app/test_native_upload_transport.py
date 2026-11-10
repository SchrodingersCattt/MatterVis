from __future__ import annotations

from pathlib import Path

ASSET = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "native_upload.js"


def test_upload_scene_activation_does_not_open_transient_websocket():
    """The persistent figure socket is the only upload-adjacent WS client."""
    source = ASSET.read_text(encoding="utf-8")
    wait_start = source.index("async function waitForSceneAndSwitch")
    wait_body = source[wait_start:source.index("\n  function uploadOne", wait_start)]

    assert "fetch(\"/api/v2/scenes\"" in wait_body
    assert "new WebSocket" not in wait_body
    assert "await waitForSceneAndSwitch(names, sceneId)" in source


def test_upload_scene_sync_runs_even_without_dash_set_props():
    source = ASSET.read_text(encoding="utf-8")
    start = source.index("async function forceActiveSceneFromServer")
    body = source[start:source.index("\n  /* Active wait", start)]
    assert "triggerDashSync(payload" in body
    assert "if (activeId && window.dash_clientside" in body
