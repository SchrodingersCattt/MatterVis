from __future__ import annotations

from mat_viewer.app import create_app
from mat_viewer.render.picking import atom_instance_id


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


def test_selection_scope_expands_atom_source_and_excludes_hidden_rows():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    backend = app.crystal_backend
    scene = backend.scene_for_state(backend.get_state())
    atoms = list(scene["draw_atoms"])
    assert atoms
    first = atoms[0]
    target = {
        "kind": "atom",
        "id": atom_instance_id(first, 0),
        "source_index": first.get("_source_index", first.get("source_index", 0)),
    }
    response = client.post("/api/v2/selection/scope", json={"target": target, "scope": "source"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["scope"] == "source"
    assert body["count"] == len(body["targets"])
    assert body["selection"]["targets"] == body["targets"]

    hidden = str(first["label"])
    backend.patch_state(
        {
            "atom_groups": [
                {
                    "id": "hide-scope-test",
                    "selector": {"labels": [hidden]},
                    "visible": False,
                    "enabled": True,
                }
            ]
        }
    )
    response = client.post("/api/v2/selection/scope", json={"target": target, "scope": "source"})
    assert response.status_code == 200
    labels = {item.get("label") for item in response.get_json()["targets"]}
    assert hidden not in labels


def test_selection_scope_rejects_unknown_scope_and_malformed_target():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    target = {"kind": "atom", "id": "atom:source:0"}
    response = client.post("/api/v2/selection/scope", json={"target": target, "scope": "unknown"})
    assert response.status_code == 400
    response = client.post("/api/v2/selection/scope", json={"target": {}})
    assert response.status_code == 400


def test_selection_scope_rejects_kind_mismatch_without_clearing_selection():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    scene = app.crystal_backend.scene_for_state(app.crystal_backend.get_state())
    atom = scene["draw_atoms"][0]
    target = {"kind": "atom", "id": atom_instance_id(atom, 0)}
    client.post("/api/v2/selection/targets", json={"target": target})

    response = client.post(
        "/api/v2/selection/scope",
        json={"target": target, "scope": "spec"},
    )
    assert response.status_code == 400
    assert client.get("/api/v2/selection/targets").get_json()["selection"]["targets"] == [target]


def test_geometry_selection_and_override_survive_unrelated_identity_patch():
    app = create_app()
    app.server.config["TESTING"] = True
    client = app.server.test_client()
    target = {"kind": "geometry", "id": "mesh-reconcile-test"}
    client.post("/api/v2/selection/targets", json={"target": target})
    response = client.patch(
        "/api/v2/style_overrides",
        json={"target_id": target["id"], "kind": target["kind"], "style": {"color": "#123456"}},
    )
    assert response.status_code == 200

    app.crystal_backend.patch_state(
        {
            "atom_groups": [
                {
                    "id": "reconcile-noop",
                    "selector": {"labels": ["__does-not-exist__"]},
                    "visible": True,
                }
            ]
        }
    )
    selection = client.get("/api/v2/selection/targets").get_json()["selection"]
    assert selection["targets"] == [target]
    assert client.get("/api/v2/style_overrides").get_json()["style_overrides"][target["id"]]["color"] == "#123456"


def test_appearance_reports_effective_fields_and_scope_preview_is_read_only():
    app = create_app()
    backend = app.crystal_backend
    try:
        client = app.server.test_client()
        scene = backend.scene_for_state(backend.get_state())
        atom = scene["draw_atoms"][0]
        target = {"kind": "atom", "id": atom_instance_id(atom)}
        before = backend.get_selection()
        preview = client.post("/api/v2/selection/scope", json={"target": target, "scope": "source", "commit": False})
        assert preview.status_code == 200
        assert preview.get_json()["count"] >= 1
        assert backend.get_selection() == before
        appearance = client.post("/api/v2/selection/appearance", json={"targets": [target]})
        row = appearance.get_json()["items"][0]
        assert row["effective"]["color"]
        assert row["overrides"] == {}
        assert "representation" in row["supported_fields"]
        assert "edge_width" not in row["supported_fields"]
        # An inherited no-op must not turn into a saved colour snapshot.
        backend.set_style_override(target["id"], "atom", {"color": row["effective"]["color"]})
        assert target["id"] not in backend.get_state().get("style_overrides", {})
        stale = {**target, "id": "atom:no-longer-present", "source_index": atom.get("_source_index", 0)}
        assert backend.expand_selection_scope(stale, "source", commit=False)["targets"] == []
    finally:
        backend.close()


def test_scene_geometry_and_saved_styles_round_trip_and_stale_ids_reconcile():
    from mat_viewer.renderer import mesh_entity
    app = create_app()
    backend = app.crystal_backend
    try:
        entity = mesh_entity([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], entity_id="mesh-instance")
        backend.patch_state({"geometry_entities": [entity]})
        backend.set_style_override("mesh-instance", "geometry", {"color": "#aa6688", "opacity": .35})
        backend.set_target_selection({"kind": "geometry", "id": "mesh-instance"})
        state = backend.get_state()
        assert state["geometry_entities"][0]["id"] == "mesh-instance"
        assert state["style_overrides"]["mesh-instance"]["opacity"] == .35
        backend.patch_state({"geometry_entities": []})
        assert backend.get_selection()["targets"] == []
        assert "mesh-instance" not in backend.get_state()["style_overrides"]
    finally:
        backend.close()


def test_target_revision_rejects_stale_style_but_survives_cosmetic_selection():
    app = create_app()
    backend = app.crystal_backend
    try:
        client = app.server.test_client()
        target = {"kind": "geometry", "id": "mesh"}
        report = backend.inspect_appearance([target])
        revision = report["target_revision"]
        backend.set_target_selection(target)
        assert backend.inspect_appearance([target])["target_revision"] == revision
        stale = client.patch("/api/v2/style_overrides", json={"targets": [target], "style": {"color": "#aa6688"}, "target_revision": revision+1})
        assert stale.status_code == 409
        assert not backend.get_state().get("style_overrides")
        from mat_viewer.app.rightclick import _dispatch_rightclick_action
        _dispatch_rightclick_action(backend, backend.active_scene_id(), "set_color", "geometry", target,
                                   {"target_revision": revision+1}, color="#aa6688")
        assert not backend.get_state().get("style_overrides")
    finally:
        backend.close()


def test_facet_family_scope_normalizes_miller_and_accepts_minimal_targets(monkeypatch):
    from mat_viewer.render.picking import facet_instance_id

    app = create_app()
    backend = app.crystal_backend
    try:
        facets = [{"miller": (1, 0, 0)}, {"miller": [1, 0, 0]},
                  {"miller": [-1, 0, 0]}, {"miller": [0, 1, 0]}]
        monkeypatch.setattr(backend, "scene_for_state", lambda state: {"bfdh_morphology": {"facets": facets}})
        target = {"kind": "facet", "id": facet_instance_id([1, 0, 0], 0)}
        before = backend.get_selection()
        client = app.server.test_client()
        for payload in ({**target, "miller": [1, 0, 0]}, target):
            response = client.post("/api/v2/selection/scope", json={
                "target": payload, "scope": "facet_family", "commit": False,
            })
            assert response.status_code == 200
            assert [row["id"] for row in response.get_json()["targets"]] == [
                facet_instance_id([1, 0, 0], index) for index in (0, 1)
            ]
            assert backend.get_selection() == before
        # An unparseable identity must never expand to the whole morphology.
        result = backend.expand_selection_scope({"kind": "facet", "id": "unknown"}, "facet_family", commit=False)
        assert result["targets"] == [{"kind": "facet", "id": "unknown"}]
    finally:
        backend.close()
