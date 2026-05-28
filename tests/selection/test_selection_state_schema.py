from __future__ import annotations

from crystal_viewer.app import create_app


def test_selection_backend_replace_add_remove_clear():
    backend = create_app().crystal_backend
    scene_id = backend.active_scene_id()
    labels = [atom["label"] for atom in backend.scene_for_state(backend.get_state(scene_id))["draw_atoms"][:3]]

    assert backend.get_selection(scene_id)["atom_labels"] == []
    assert backend.set_selection(labels[:1], scene_id=scene_id)["atom_labels"] == labels[:1]
    assert backend.add_to_selection(labels[1:3], scene_id=scene_id)["atom_labels"] == labels[:3]
    assert backend.remove_from_selection(labels[1:2], scene_id=scene_id)["atom_labels"] == [labels[0], labels[2]]
    assert backend.clear_selection(scene_id)["atom_labels"] == []


def test_selection_is_scoped_per_scene():
    backend = create_app().crystal_backend
    first = backend.active_scene_id()
    second = backend.create_scene(structure=backend.get_state()["structure"], label="selection second")["id"]
    first_label = backend.scene_for_state(backend.get_state(first))["draw_atoms"][0]["label"]

    backend.set_selection([first_label], scene_id=first)

    assert backend.get_selection(first)["atom_labels"] == [first_label]
    assert backend.get_selection(second)["atom_labels"] == []
