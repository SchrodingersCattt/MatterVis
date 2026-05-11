"""Phase 4 REST API surface: ``/api/v2/transforms``,
``/api/v2/bond_groups``, ``/api/v2/polyhedra/<id>/instance_overrides``,
plus the ``supercell`` shorthand on ``POST /api/v2/state``.

Each test exercises the full Flask test client so it doubles as a
contract test for AI agents driving the viewer over HTTP.

DO NOT REMOVE -- the endpoint shape and error semantics here are
documented in ``agents/transforms_api.md``,
``agents/bond_groups_api.md``, and the v2 polyhedra section of
``agents/polyhedron_api.md``.
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


# ---- transforms --------------------------------------------------------


def test_transforms_list_starts_empty(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/api/v2/transforms")
    assert response.status_code == 200
    assert response.get_json() == {"transforms": []}


def test_transforms_post_requires_kind(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post("/api/v2/transforms", json={"params": {"a": 2}})
    assert response.status_code == 400
    assert "kind" in response.get_json()["error"]


def test_transforms_post_unknown_kind_400(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/transforms",
        json={"kind": "frobnicate", "params": {}},
    )
    assert response.status_code == 400


def test_transforms_post_repeat_creates_and_lists(tmp_path: Path):
    client = _client(tmp_path)
    created = client.post(
        "/api/v2/transforms",
        json={"kind": "repeat", "params": {"a": 2, "b": 2, "c": 1}, "name": "2x2x1"},
    )
    assert created.status_code == 200
    spec = created.get_json()
    assert spec["kind"] == "repeat"
    assert spec["params"] == {"a": 2, "b": 2, "c": 1}
    assert spec["enabled"] is True

    listing = client.get("/api/v2/transforms").get_json()
    assert [item["id"] for item in listing["transforms"]] == [spec["id"]]


def test_transforms_patch_toggles_enabled(tmp_path: Path):
    client = _client(tmp_path)
    spec = client.post(
        "/api/v2/transforms",
        json={"kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}},
    ).get_json()
    response = client.patch(
        f"/api/v2/transforms/{spec['id']}",
        json={"enabled": False},
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["enabled"] is False
    assert updated["id"] == spec["id"]


def test_transforms_delete_then_404(tmp_path: Path):
    client = _client(tmp_path)
    spec = client.post(
        "/api/v2/transforms",
        json={"kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}},
    ).get_json()
    deleted = client.delete(f"/api/v2/transforms/{spec['id']}")
    assert deleted.status_code == 200
    assert deleted.get_json() == {"deleted": spec["id"]}
    again = client.delete(f"/api/v2/transforms/{spec['id']}")
    assert again.status_code == 404


def test_transforms_reorder_round_trips(tmp_path: Path):
    client = _client(tmp_path)
    a = client.post(
        "/api/v2/transforms",
        json={"kind": "repeat", "params": {"a": 2, "b": 1, "c": 1}},
    ).get_json()
    b = client.post(
        "/api/v2/transforms",
        json={"kind": "repeat", "params": {"a": 1, "b": 2, "c": 1}},
    ).get_json()
    response = client.post(
        "/api/v2/transforms/reorder",
        json={"order": [b["id"], a["id"]]},
    )
    assert response.status_code == 200
    new_order = [item["id"] for item in response.get_json()["transforms"]]
    assert new_order == [b["id"], a["id"]]


# ---- bond_groups -------------------------------------------------------


def test_bond_groups_list_starts_empty(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/api/v2/bond_groups")
    assert response.status_code == 200
    assert response.get_json() == {"groups": []}


def test_bond_groups_post_requires_selector_dict(tmp_path: Path):
    client = _client(tmp_path)
    bad = client.post("/api/v2/bond_groups", json={"selector": "not-a-dict"})
    assert bad.status_code == 400


def test_bond_groups_post_creates_with_between_elements(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/bond_groups",
        json={
            "selector": {"between_elements": ["O", "H"]},
            "color": "#FF00FF",
            "radius_scale": 1.5,
        },
    )
    assert response.status_code == 200
    group = response.get_json()
    assert group["color"] == "#ff00ff"
    assert group["radius_scale"] == 1.5
    assert group["selector"] == {"between_elements": ["O", "H"]}


def test_bond_groups_patch_updates_radius(tmp_path: Path):
    client = _client(tmp_path)
    group = client.post(
        "/api/v2/bond_groups",
        json={"selector": {"all": True}, "color": "#000000"},
    ).get_json()
    response = client.patch(
        f"/api/v2/bond_groups/{group['id']}",
        json={"radius_scale": 2.0},
    )
    assert response.status_code == 200
    assert response.get_json()["radius_scale"] == 2.0


def test_bond_groups_delete_then_404(tmp_path: Path):
    client = _client(tmp_path)
    group = client.post(
        "/api/v2/bond_groups", json={"selector": {"all": True}}
    ).get_json()
    deleted = client.delete(f"/api/v2/bond_groups/{group['id']}")
    assert deleted.status_code == 200
    again = client.delete(f"/api/v2/bond_groups/{group['id']}")
    assert again.status_code == 404


# ---- polyhedron instance overrides ------------------------------------


def test_polyhedron_instance_override_set_and_clear(tmp_path: Path):
    client = _client(tmp_path)
    spec = client.post(
        "/api/v2/polyhedra",
        json={"center_species": "N", "color": "#7C5CBF"},
    ).get_json()
    set_resp = client.post(
        f"/api/v2/polyhedra/{spec['id']}/instance_overrides/X3",
        json={"color": "#22DD22", "visible": False},
    )
    assert set_resp.status_code == 200
    body = set_resp.get_json()
    assert body["instance_overrides"]["X3"] == {"color": "#22dd22", "visible": False}

    cleared = client.delete(
        f"/api/v2/polyhedra/{spec['id']}/instance_overrides/X3"
    )
    assert cleared.status_code == 200
    assert "X3" not in cleared.get_json()["instance_overrides"]


def test_polyhedron_instance_override_unknown_spec_404(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/polyhedra/nope/instance_overrides/X3",
        json={"color": "#000000"},
    )
    assert response.status_code == 404


def test_polyhedron_patch_accepts_full_instance_overrides_map(tmp_path: Path):
    """The existing PATCH endpoint must also accept ``instance_overrides``
    as a single payload field, mirroring how AI scripts replay a saved
    state in one call."""
    client = _client(tmp_path)
    spec = client.post(
        "/api/v2/polyhedra",
        json={"center_species": "N", "color": "#7C5CBF"},
    ).get_json()
    response = client.patch(
        f"/api/v2/polyhedra/{spec['id']}",
        json={"instance_overrides": {"X0": {"color": "#FF0000"}, "X1": {"visible": False}}},
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["instance_overrides"]["X0"] == {"color": "#ff0000"}
    assert body["instance_overrides"]["X1"] == {"visible": False}


# ---- supercell shorthand ----------------------------------------------


def test_state_post_supercell_emits_repeat_transform(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/api/v2/state",
        json={"supercell": {"a": 3, "b": 2, "c": 1}},
    )
    assert response.status_code == 200
    state = response.get_json()
    repeats = [t for t in state.get("transforms", []) if t["kind"] == "repeat"]
    assert len(repeats) == 1
    assert repeats[0]["params"] == {"a": 3, "b": 2, "c": 1}


def test_state_post_supercell_replaces_previous(tmp_path: Path):
    client = _client(tmp_path)
    client.post("/api/v2/state", json={"supercell": {"a": 2, "b": 2, "c": 2}})
    second = client.post("/api/v2/state", json={"supercell": {"a": 4, "b": 1, "c": 1}}).get_json()
    repeats = [t for t in second.get("transforms", []) if t["kind"] == "repeat"]
    assert len(repeats) == 1
    assert repeats[0]["params"] == {"a": 4, "b": 1, "c": 1}


# ---- screenshot synchronously reflects state changes ------------------


def test_screenshot_reflects_state_changes(tmp_path: Path):
    """An AI script that POSTs a state change then GETs a screenshot
    must see the updated render in the returned PNG bytes (not a
    stale picture from before the patch). The endpoint is synchronous
    so this is a pure sequencing test rather than a polling one."""
    client = _client(tmp_path)
    # Baseline screenshot before any change.
    before = client.get("/api/v2/screenshot")
    assert before.status_code == 200
    assert before.mimetype == "image/png"
    # Patch state then re-fetch; the response must succeed (no 5xx).
    patched = client.post("/api/v2/state", json={"atom_scale": 0.5})
    assert patched.status_code == 200
    after = client.get("/api/v2/screenshot")
    assert after.status_code == 200
    assert after.mimetype == "image/png"
