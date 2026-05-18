from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


def test_close_tab_during_neighbor_style_update_is_not_lost(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    first = backend.active_scene_id()
    duplicate = backend.apply_intent(
        {
            "type": "crud_scene",
            "client_id": "client-a",
            "client_seq": 1,
            "scene_id": first,
            "payload": {"action": "duplicate"},
        }
    )["scene"]["id"]

    backend.apply_intent(
        {
            "type": "set_style",
            "client_id": "client-a",
            "client_seq": 2,
            "scene_id": duplicate,
            "payload": {"atom_scale": 1.35},
        }
    )
    backend.apply_intent(
        {
            "type": "crud_scene",
            "client_id": "client-a",
            "client_seq": 3,
            "scene_id": first,
            "payload": {"action": "delete"},
        }
    )

    scene_ids = {scene["id"] for scene in backend.scene_options()}
    assert first not in scene_ids
    assert duplicate in scene_ids
    assert backend.get_state(duplicate)["atom_scale"] == 1.35
