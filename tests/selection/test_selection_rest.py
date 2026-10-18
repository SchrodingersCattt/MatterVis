from __future__ import annotations

from mat_viewer.app import create_app


def test_selection_rest_round_trip_and_promote():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    backend = app.crystal_backend
    label = backend.scene_for_state(backend.get_state())["draw_atoms"][0]["label"]

    response = client.post("/api/v2/selection", json={"atom_labels": [label]})
    assert response.status_code == 200
    assert response.get_json()["selection"]["atom_labels"] == [label]

    response = client.patch("/api/v2/selection", json={"remove": [label]})
    assert response.status_code == 200
    assert response.get_json()["selection"]["atom_labels"] == []

    client.post("/api/v2/selection", json={"atom_labels": [label]})
    response = client.post("/api/v2/selection/promote", json={"name": "picked", "color": "#FFD24A"})
    assert response.status_code == 200
    assert response.get_json()["group_id"]
    assert backend.get_selection()["atom_labels"] == []


def test_selection_by_fragment_and_element():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    scene = app.crystal_backend.scene_for_state(app.crystal_backend.get_state())
    fragment_label = next(label for label in scene.get("atom_fragment_labels", []) if label)
    element = scene["draw_atoms"][0]["elem"]

    response = client.post("/api/v2/selection/by_fragment", json={"fragment_label": fragment_label})
    assert response.status_code == 200
    assert response.get_json()["selection"]["atom_labels"]

    response = client.post("/api/v2/selection/by_element", json={"element": element})
    assert response.status_code == 200
    assert response.get_json()["selection"]["atom_labels"]


def test_semantic_targets_and_style_overrides_round_trip():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    target = {"kind": "geometry", "id": "mesh-test"}
    response = client.post("/api/v2/selection/targets", json={"target": target})
    assert response.status_code == 200
    assert response.get_json()["selection"]["targets"][0]["id"] == "mesh-test"

    response = client.patch(
        "/api/v2/style_overrides",
        json={"target_id": "mesh-test", "kind": "geometry", "style": {"color": "#123456", "opacity": 0.4}},
    )
    assert response.status_code == 200
    assert response.get_json()["style_overrides"]["mesh-test"]["color"] == "#123456"
    response = client.patch(
        "/api/v2/style_overrides",
        json={"target_id": "mesh-test", "kind": "geometry", "style": {"opacity": 9, "edge_width": "bad"}},
    )
    assert response.status_code == 400
    assert any(field in response.get_json()["error"] for field in ("opacity", "edge_width"))
    unchanged = client.get("/api/v2/style_overrides").get_json()["style_overrides"]["mesh-test"]
    assert unchanged["opacity"] == 0.4
    invalid = client.patch(
        "/api/v2/style_overrides",
        json={"target_id": "mesh-test", "kind": "geometry", "style": {"bogus": "value"}},
    )
    assert invalid.status_code == 400
    assert "unsupported style field" in invalid.get_json()["error"]
    missing_kind = client.patch(
        "/api/v2/style_overrides",
        json={"target_id": "mesh-test", "style": {"color": "#abcdef"}},
    )
    assert missing_kind.status_code == 400
    empty = client.patch(
        "/api/v2/style_overrides",
        json={"target_id": "mesh-test", "kind": "geometry", "style": {}},
    )
    assert empty.status_code == 400
    response = client.patch("/api/v2/style_overrides", json={"target_id": "mesh-test", "reset": True})
    assert response.get_json()["style_overrides"] == {}


def test_multi_target_style_patch_uses_common_field_intersection():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    targets = [
        {"kind": "atom", "id": "atom-test"},
        {"kind": "polyhedron", "id": "poly-test"},
    ]
    response = client.patch(
        "/api/v2/style_overrides",
        json={"targets": targets, "style": {"color": "#123456"}},
    )
    assert response.status_code == 200
    overrides = response.get_json()["style_overrides"]
    assert overrides["atom-test"]["color"] == "#123456"
    assert overrides["poly-test"]["color"] == "#123456"

    invalid = client.patch(
        "/api/v2/style_overrides",
        json={"targets": targets, "style": {"radius": 0.2}},
    )
    assert invalid.status_code == 400
    assert "selection" in invalid.get_json()["error"]

    reset = client.patch(
        "/api/v2/style_overrides",
        json={"targets": targets, "reset": True, "field": "color"},
    )
    assert reset.status_code == 200
    assert reset.get_json()["style_overrides"] == {}


def test_semantic_target_toggle_removes_target_without_reinserting_it():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()

    first = {"kind": "geometry", "id": "mesh-first"}
    second = {"kind": "geometry", "id": "mesh-second"}
    client.post("/api/v2/selection/targets", json={"target": first})
    response = client.post(
        "/api/v2/selection/targets",
        json={"target": second, "additive": True},
    )
    assert response.get_json()["selection"]["targets"] == [first, second]

    response = client.post(
        "/api/v2/selection/targets",
        json={"target": second, "additive": True, "toggle": True},
    )
    selection = response.get_json()["selection"]
    assert selection["targets"] == [first]
    assert selection["active_target"] == first

    response = client.post(
        "/api/v2/selection/targets",
        json={"target": first, "additive": True, "toggle": True},
    )
    selection = response.get_json()["selection"]
    assert selection["targets"] == []
    assert selection["active_target"] is None
