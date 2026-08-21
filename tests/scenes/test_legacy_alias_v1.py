from __future__ import annotations

from mat_viewer.app import ViewerBackend


def test_v1_legacy_aliases_survive_scene_state(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    state = backend.patch_state(
        {
            "display_mode": "cluster",
            "topology_fragment_type": "A",
            "topology_show_all_sites": True,
        }
    )
    assert state["display_mode"] == "cluster"
    assert "scene_id" in state
