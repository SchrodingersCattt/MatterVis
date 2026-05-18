from __future__ import annotations

import json
from pathlib import Path

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


def test_record_state_marks_dirty_without_saving_under_backend_lock(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    backend._persist_stop.set()
    calls = {"n": 0}

    def fake_save():
        assert not backend._lock.locked()
        calls["n"] += 1
        backend.scene_store.mark_dirty()
        backend.scene_store._dirty = False

    backend.scene_store.save = fake_save  # type: ignore[method-assign]

    backend.record_state({"atom_scale": 1.23})

    assert calls["n"] == 0
    assert backend.scene_store.is_dirty()

    backend.flush_scene_store()

    assert calls["n"] == 1


def test_flush_scene_store_persists_latest_state(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    backend._persist_stop.set()

    backend.record_state({"atom_scale": 1.37})
    backend.flush_scene_store()

    payload = json.loads(Path(backend.scene_store.path).read_text(encoding="utf-8"))
    active_id = payload["active_id"]
    scene = next(item for item in payload["scenes"] if item["id"] == active_id)
    assert scene["state_patch"]["atom_scale"] == 1.37
