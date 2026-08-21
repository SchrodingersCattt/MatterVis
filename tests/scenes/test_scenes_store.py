from __future__ import annotations

import pytest

from mat_viewer.scenes import SceneStore


def test_scene_store_crud_round_trip(tmp_path):
    path = tmp_path / "scenes.json"
    store = SceneStore(str(path))
    first = store.add(label="A", structure_name="DAP-4", state_patch={"display_mode": "unit_cell"})
    second = store.duplicate(first.id, label="B")

    assert store.active_id == second.id
    assert store.order == [first.id, second.id]

    store.rename(first.id, "A renamed")
    assert store.get(first.id).label == "A renamed"

    store.reorder([second.id, first.id])
    assert store.order == [second.id, first.id]
    store.save()

    loaded = SceneStore.load(str(path))
    assert loaded.order == [second.id, first.id]
    assert loaded.get(first.id).state_patch["display_mode"] == "unit_cell"


def test_scene_store_rejects_invalid_mutations(tmp_path):
    store = SceneStore(str(tmp_path / "scenes.json"))
    first = store.add(label="A", structure_name="DAP-4")
    second = store.add(label="B", structure_name="DAP-4")

    with pytest.raises(ValueError):
        store.rename(second.id, "A")
    with pytest.raises(ValueError):
        store.rename(second.id, "")
    with pytest.raises(ValueError):
        store.reorder([first.id])
