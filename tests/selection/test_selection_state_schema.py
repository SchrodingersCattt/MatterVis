from __future__ import annotations

from mat_viewer.app import create_app


def test_selection_backend_replace_add_remove_clear():
    backend = create_app().crystal_backend
    scene_id = backend.active_scene_id()
    labels = [atom["label"] for atom in backend.scene_for_state(backend.get_state(scene_id))["draw_atoms"][:3]]

    assert backend.get_selection(scene_id)["atom_labels"] == []
    assert backend.set_selection(labels[:1], scene_id=scene_id)["atom_labels"] == labels[:1]
    assert backend.add_to_selection(labels[1:3], scene_id=scene_id)["atom_labels"] == labels[:3]
    assert backend.remove_from_selection(labels[1:2], scene_id=scene_id)["atom_labels"] == [labels[0], labels[2]]
    cleared = backend.clear_selection(scene_id)
    assert cleared["atom_labels"] == []
    assert cleared["targets"] == []
    assert cleared["active_target"] is None


def test_older_selection_gesture_does_not_overwrite_a_newer_one():
    backend = create_app().crystal_backend
    scene_id = backend.active_scene_id()
    revision = backend.get_state(scene_id).get("target_revision", 0)
    newer = {"kind": "atom", "id": "atom:newer"}
    older = {"kind": "atom", "id": "atom:older"}

    assert backend._commit_selection_event(
        {"targets": [newer], "mode": "replace", "target_revision": revision, "ts": 200},
        scene_id,
    )
    assert backend.get_selection(scene_id)["targets"][0]["id"] == "atom:newer"
    assert backend.style_for_state(backend.get_state(scene_id))["_selection_gesture_ts"] == 200

    assert not backend._commit_selection_event(
        {"targets": [older], "mode": "replace", "target_revision": revision, "ts": 100},
        scene_id,
    )
    assert backend.get_selection(scene_id)["targets"][0]["id"] == "atom:newer"
    assert backend._commit_selection_event(
        {"targets": [older], "mode": "replace", "target_revision": revision, "ts": True},
        scene_id,
    )
    # A boolean is not a clock, so this payload still commits and can replace the set.
    assert backend.get_selection(scene_id)["targets"][0]["id"] == "atom:older"

    assert backend._commit_selection_event(
        {"targets": [], "mode": "clear", "target_revision": revision, "ts": 300},
        scene_id,
    )
    assert backend.get_selection(scene_id)["targets"] == []
    assert backend.style_for_state(backend.get_state(scene_id))["_selection_gesture_ts"] == 300

    assert backend._commit_selection_event(
        {"targets": [newer], "mode": "replace", "target_revision": revision},
        scene_id,
    )
    assert backend.get_selection(scene_id)["targets"][0]["id"] == "atom:newer"
    assert not backend._commit_selection_event(
        {"targets": [older], "mode": "replace", "target_revision": revision, "ts": 150},
        scene_id,
    )
    assert backend.get_selection(scene_id)["targets"][0]["id"] == "atom:newer"


def test_selection_is_scoped_per_scene():
    backend = create_app().crystal_backend
    first = backend.active_scene_id()
    second = backend.create_scene(structure=backend.get_state()["structure"], label="selection second")["id"]
    first_label = backend.scene_for_state(backend.get_state(first))["draw_atoms"][0]["label"]

    backend.set_selection([first_label], scene_id=first)

    assert backend.get_selection(first)["atom_labels"] == [first_label]
    assert backend.get_selection(second)["atom_labels"] == []
