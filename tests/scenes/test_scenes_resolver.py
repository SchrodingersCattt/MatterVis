from __future__ import annotations

from mat_viewer.app import ViewerBackend


def test_backend_scene_state_targets_active_and_explicit_scene(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    placeholder = backend.active_scene_id()
    assert placeholder is not None

    scene = backend.create_scene(structure=backend.get_state()["structure"], label="Second")
    backend.patch_state({"display_mode": "cluster"}, scene_id=scene["id"])

    assert backend.get_state(scene["id"])["display_mode"] == "cluster"
    assert backend.get_state(placeholder)["display_mode"] == "formula_unit"
    assert backend.active_scene_id() == scene["id"]
