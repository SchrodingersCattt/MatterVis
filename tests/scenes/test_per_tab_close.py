from __future__ import annotations

from mat_viewer.app import ViewerBackend


def _has_component_id(component, target_id) -> bool:
    if getattr(component, "id", None) == target_id:
        return True
    children = getattr(component, "children", None)
    label = getattr(component, "label", None)
    if label is not None:
        children = [label] if children is None else [children, label]
    if children is None:
        return False
    if not isinstance(children, (list, tuple)):
        children = [children]
    return any(_has_component_id(child, target_id) for child in children)


def test_scene_tabs_render_stable_tab_and_close_ids(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    scene_id = backend.active_scene_id()

    tabs = backend.scene_tabs()
    close_buttons = backend.scene_close_buttons()

    assert _has_component_id(tabs[0], f"scene-tab-{scene_id}")
    assert _has_component_id(close_buttons[0], f"scene-tab-close-{scene_id}")


def test_deleting_active_scene_falls_through_to_next_scene(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    first = backend.active_scene_id()
    second = backend.create_scene(structure=backend.get_state()["structure"], label="Second")["id"]

    backend.delete_scene(second)

    assert backend.active_scene_id() == first
    assert second not in {item["id"] for item in backend.scene_options()}
