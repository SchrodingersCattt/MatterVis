"""Phase 2 atom_groups -- REST API surface.

Covers ``GET / POST / PATCH / DELETE /api/v2/atom_groups`` plus the
``/api/v2/atom_groups/reorder`` helper. Each test exercises the full
Flask test client so it doubles as a contract test for agents driving
atom-group overrides over HTTP.

DO NOT REMOVE -- the endpoint shape and error semantics here are
documented in ``agents/atom_groups_api.md``.
"""
from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import WORKSPACE_DIR, create_app


def _client(tmp_path: Path):
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=WORKSPACE_DIR,
    )
    return app.server.test_client()


def test_atom_groups_list_starts_empty(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/api/v2/atom_groups")
    assert response.status_code == 200
    assert response.get_json() == {"groups": []}


def test_atom_groups_post_requires_selector(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post("/api/v2/atom_groups", json={"name": "no selector"})
    assert response.status_code == 400


def test_atom_groups_post_rejects_empty_selector(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post("/api/v2/atom_groups", json={"selector": {}})
    assert response.status_code == 400


def test_atom_groups_post_creates_and_lists(tmp_path: Path):
    client = _client(tmp_path)
    created = client.post(
        "/api/v2/atom_groups",
        json={
            "name": "all-grey",
            "selector": {"all": True},
            "color": "#888888",
        },
    ).get_json()
    assert created["color"] == "#888888"
    assert created["selector"] == {"all": True}

    listing = client.get("/api/v2/atom_groups").get_json()
    assert [g["id"] for g in listing["groups"]] == [created["id"]]


def test_atom_groups_patch_overrides_fields(tmp_path: Path):
    client = _client(tmp_path)
    group = client.post(
        "/api/v2/atom_groups",
        json={"selector": {"elements": ["O"]}, "color": "#FF0000"},
    ).get_json()
    response = client.patch(
        f"/api/v2/atom_groups/{group['id']}",
        json={"color": "#00FF00", "visible": False, "opacity": 0.4, "material": "flat"},
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["color"] == "#00ff00"
    assert updated["visible"] is False
    assert updated["opacity"] == 0.4
    assert updated["material"] == "flat"
    assert updated["id"] == group["id"]


def test_atom_groups_patch_unknown_id_returns_404(tmp_path: Path):
    client = _client(tmp_path)
    response = client.patch("/api/v2/atom_groups/not_real", json={"color": "#000000"})
    assert response.status_code == 404


def test_atom_groups_delete(tmp_path: Path):
    client = _client(tmp_path)
    group = client.post(
        "/api/v2/atom_groups", json={"selector": {"all": True}}
    ).get_json()
    response = client.delete(f"/api/v2/atom_groups/{group['id']}")
    assert response.status_code == 200
    assert response.get_json() == {"deleted": group["id"]}
    assert client.get("/api/v2/atom_groups").get_json()["groups"] == []


def test_atom_groups_reorder(tmp_path: Path):
    client = _client(tmp_path)
    a = client.post("/api/v2/atom_groups", json={"selector": {"all": True}}).get_json()
    b = client.post("/api/v2/atom_groups", json={"selector": {"elements": ["O"]}}).get_json()
    response = client.post("/api/v2/atom_groups/reorder", json={"order": [b["id"], a["id"]]})
    assert response.status_code == 200
    assert [g["id"] for g in response.get_json()["groups"]] == [b["id"], a["id"]]


def test_atom_groups_scoped_to_scene_id(tmp_path: Path):
    client = _client(tmp_path)
    boot = client.get("/api/v2/scenes").get_json()
    boot_id = boot["active_id"]
    other = client.post(
        "/api/v2/scenes", json={"structure": "DAP-4", "label": "tab 2"}
    ).get_json()
    spec_other = client.post(
        f"/api/v2/atom_groups?scene_id={other['id']}",
        json={"selector": {"elements": ["H"]}, "name": "in-tab-2"},
    ).get_json()
    assert spec_other["name"] == "in-tab-2"

    listing_boot = client.get(
        f"/api/v2/atom_groups?scene_id={boot_id}"
    ).get_json()["groups"]
    listing_other = client.get(
        f"/api/v2/atom_groups?scene_id={other['id']}"
    ).get_json()["groups"]
    assert listing_boot == []
    assert [g["id"] for g in listing_other] == [spec_other["id"]]
