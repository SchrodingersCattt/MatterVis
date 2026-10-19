from __future__ import annotations

from mat_viewer.app import ViewerBackend


def _backend(tmp_path):
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )


def test_undo_restores_closed_scene_and_active_tab(tmp_path):
    backend = _backend(tmp_path)
    try:
        original = backend.active_scene_id()
        duplicate = backend.duplicate_scene(original)["id"]
        assert backend.active_scene_id() == duplicate
        generation_before_close = backend.scene_generation(duplicate)

        backend.delete_scene(duplicate)
        assert duplicate not in {item["id"] for item in backend.scene_options()}

        result = backend.undo_scene_close()
        assert result["restored"] == [duplicate]
        assert backend.active_scene_id() == duplicate
        assert [item["id"] for item in backend.scene_options()] == [
            original,
            duplicate,
        ]
        assert backend.scene_generation(duplicate) > generation_before_close
    finally:
        backend.close()


def test_undo_restores_all_tabs_removed_by_close_others(tmp_path):
    backend = _backend(tmp_path)
    try:
        first = backend.active_scene_id()
        second = backend.duplicate_scene(first)["id"]
        third = backend.duplicate_scene(second)["id"]
        backend.delete_other_scenes(first)
        assert [item["id"] for item in backend.scene_options()] == [first]

        result = backend.undo_scene_close()
        assert set(result["restored"]) == {second, third}
        assert [item["id"] for item in backend.scene_options()] == [
            first,
            second,
            third,
        ]
        assert backend.active_scene_id() == third
    finally:
        backend.close()
