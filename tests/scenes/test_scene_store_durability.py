"""Scene-store writes must not drop a mutation that lands during the replace."""

from __future__ import annotations

from mat_viewer.scenes import SceneStore
from mat_viewer.scenes import core as scene_core


def test_save_keeps_a_mutation_that_lands_during_the_write(tmp_path, monkeypatch):
    store = SceneStore(str(tmp_path / "scenes.json"))
    store.add(label="A", structure_name="DAP-4", save=False)
    store.mark_dirty()
    extra: dict[str, str] = {}
    original = scene_core.json.dump

    def dumping(payload, handle, **kwargs):
        if "id" not in extra:
            scene = store.add(label="B", structure_name="DAP-4", save=False)
            store.mark_dirty()
            extra["id"] = scene.id
        original(payload, handle, **kwargs)

    monkeypatch.setattr(scene_core.json, "dump", dumping)

    store.save()

    assert store.is_dirty()
    assert extra["id"] not in _scene_ids(store.path)
    store.save()
    assert extra["id"] in _scene_ids(store.path)
    assert store.is_dirty() is False


def _scene_ids(path: str) -> list[str]:
    loaded = SceneStore.load(path)
    return list(loaded.order)
