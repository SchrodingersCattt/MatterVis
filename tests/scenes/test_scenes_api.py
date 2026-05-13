from __future__ import annotations

from crystal_viewer.app import create_app


def test_v2_scene_crud_and_state_targeting(tmp_path):
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )
    client = app.server.test_client()

    response = client.get("/api/v2/scenes")
    assert response.status_code == 200
    scenes = response.get_json()["scenes"]
    active_id = response.get_json()["active_id"]
    assert active_id

    created = client.post("/api/v2/scenes", json={"structure": "__upload__", "label": "API scene"}).get_json()
    assert created["label"] == "API scene"

    patch = client.post(f"/api/v2/state?scene_id={created['id']}", json={"display_mode": "cluster"})
    assert patch.status_code == 200
    assert patch.get_json()["display_mode"] == "cluster"
    assert client.get(f"/api/v2/state?scene_id={created['id']}").get_json()["display_mode"] == "cluster"
    assert client.get(f"/api/v2/state?scene_id={active_id}").get_json()["display_mode"] == "formula_unit"

    duplicate = client.post(f"/api/v2/scenes/{created['id']}/duplicate", json={"label": "Copy"}).get_json()
    assert duplicate["label"] == "Copy"
    order = [item["id"] for item in client.get("/api/v2/scenes").get_json()["scenes"]]
    assert client.post("/api/v2/scenes/reorder", json={"order": list(reversed(order))}).status_code == 200
    assert client.delete(f"/api/v2/scenes/{duplicate['id']}").status_code == 200


def test_v1_state_shim_targets_active_scene(tmp_path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    client = app.server.test_client()
    response = client.post("/api/v1/state", json={"display_mode": "cluster"})
    assert response.status_code == 200
    assert client.get("/api/v1/state").get_json()["display_mode"] == "cluster"


def test_v2_scenes_close_others_bulk_closes(tmp_path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    client = app.server.test_client()

    base = client.get("/api/v2/scenes").get_json()
    base_id = base["active_id"]
    a = client.post("/api/v2/scenes", json={"structure": "__upload__", "label": "A"}).get_json()
    b = client.post("/api/v2/scenes", json={"structure": "__upload__", "label": "B"}).get_json()
    c = client.post("/api/v2/scenes", json={"structure": "__upload__", "label": "C"}).get_json()
    keep_id = b["id"]
    client.post("/api/v2/scenes/active", json={"scene_id": keep_id})

    payload = client.post("/api/v2/scenes/close_others", json={"keep": keep_id}).get_json()
    assert payload["kept"]["id"] == keep_id
    removed_ids = {entry["id"] for entry in payload["removed"]}
    assert removed_ids == {base_id, a["id"], c["id"]}

    after = client.get("/api/v2/scenes").get_json()
    assert [scene["id"] for scene in after["scenes"]] == [keep_id]
    assert after["active_id"] == keep_id

    fallback = client.post("/api/v2/scenes/close_others", json={}).get_json()
    assert fallback["kept"]["id"] == keep_id
    assert fallback["removed"] == []
