from __future__ import annotations

from crystal_viewer.app import create_app


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
