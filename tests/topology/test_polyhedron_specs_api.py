"""Phase 1 polyhedron-specs -- REST API surface.

Covers ``GET / POST / PATCH / DELETE /api/v2/polyhedra`` plus the
``/api/v2/polyhedra/reorder`` helper. Each test exercises the full
Flask test client so it doubles as a contract test for agents driving
the viewer over HTTP.

DO NOT REMOVE -- the endpoint shape and error semantics here are
documented in ``agents/polyhedron_api.md``.
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


def test_polyhedra_list_starts_empty(tmp_path: Path):
    client = _client(tmp_path)
    response = client.get("/api/v2/polyhedra")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"specs": []}


def test_polyhedra_post_requires_center_species(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post("/api/v2/polyhedra", json={"name": "missing centre"})
    assert response.status_code == 400
    assert "center_species" in response.get_json()["error"]


def test_polyhedra_post_creates_and_lists(tmp_path: Path):
    client = _client(tmp_path)
    created = client.post(
        "/api/v2/polyhedra",
        json={
            "name": "ammonium",
            "center_species": "N",
            "color": "#FF0000",
        },
    )
    assert created.status_code == 200
    spec = created.get_json()
    assert spec["name"] == "ammonium"
    assert spec["color"] == "#ff0000"
    assert spec["ligand_species"] is None

    listing = client.get("/api/v2/polyhedra").get_json()
    assert [item["id"] for item in listing["specs"]] == [spec["id"]]


def test_polyhedra_patch_updates_color_and_ligand(tmp_path: Path):
    client = _client(tmp_path)
    spec = client.post(
        "/api/v2/polyhedra", json={"center_species": "N", "color": "#FF0000"}
    ).get_json()
    response = client.patch(
        f"/api/v2/polyhedra/{spec['id']}",
        json={"color": "#00FF00", "ligand_species": "ClO4", "name": "ammonium-Cl4"},
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["color"] == "#00ff00"
    assert updated["ligand_species"] == "ClO4"
    assert updated["name"] == "ammonium-Cl4"

    # The PATCH must not reassign the spec id; agents store these
    # client-side and rely on stable identifiers.
    assert updated["id"] == spec["id"]


def test_polyhedra_patch_unknown_id_returns_404(tmp_path: Path):
    client = _client(tmp_path)
    response = client.patch("/api/v2/polyhedra/not_real", json={"color": "#000000"})
    assert response.status_code == 404


def test_polyhedra_delete_removes_spec(tmp_path: Path):
    client = _client(tmp_path)
    spec = client.post("/api/v2/polyhedra", json={"center_species": "N"}).get_json()
    response = client.delete(f"/api/v2/polyhedra/{spec['id']}")
    assert response.status_code == 200
    assert response.get_json() == {"deleted": spec["id"]}
    assert client.get("/api/v2/polyhedra").get_json()["specs"] == []


def test_polyhedra_delete_unknown_returns_404(tmp_path: Path):
    client = _client(tmp_path)
    response = client.delete("/api/v2/polyhedra/not_real")
    assert response.status_code == 404


def test_polyhedra_reorder_swaps_specs(tmp_path: Path):
    client = _client(tmp_path)
    a = client.post("/api/v2/polyhedra", json={"center_species": "N"}).get_json()
    b = client.post("/api/v2/polyhedra", json={"center_species": "C6N2"}).get_json()
    response = client.post("/api/v2/polyhedra/reorder", json={"order": [b["id"], a["id"]]})
    assert response.status_code == 200
    assert [item["id"] for item in response.get_json()["specs"]] == [b["id"], a["id"]]


def test_polyhedra_reorder_with_wrong_id_set_returns_400(tmp_path: Path):
    client = _client(tmp_path)
    a = client.post("/api/v2/polyhedra", json={"center_species": "N"}).get_json()
    response = client.post("/api/v2/polyhedra/reorder", json={"order": [a["id"], "extra"]})
    assert response.status_code == 400


def test_polyhedra_scoped_to_scene_id(tmp_path: Path):
    client = _client(tmp_path)
    # Capture the boot scene id BEFORE creating a second tab; ``POST
    # /api/v2/scenes`` activates the newly created scene (matching the
    # behaviour of clicking a freshly created tab in the UI), so the
    # implicit "active scene" target shifts under our feet otherwise.
    boot = client.get("/api/v2/scenes").get_json()
    boot_id = boot["active_id"]
    other = client.post(
        "/api/v2/scenes", json={"structure": "DAP-4", "label": "tab 2"}
    ).get_json()
    spec_other = client.post(
        f"/api/v2/polyhedra?scene_id={other['id']}",
        json={"center_species": "N", "name": "in-tab-2"},
    ).get_json()
    assert spec_other["name"] == "in-tab-2"

    listing_boot = client.get(f"/api/v2/polyhedra?scene_id={boot_id}").get_json()["specs"]
    listing_other = client.get(f"/api/v2/polyhedra?scene_id={other['id']}").get_json()["specs"]
    assert listing_boot == []
    assert [item["id"] for item in listing_other] == [spec_other["id"]]
