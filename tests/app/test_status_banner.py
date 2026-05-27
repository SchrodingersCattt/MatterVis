from __future__ import annotations

from dash import dcc

from crystal_viewer.app import _status_class, _status_message, create_app


def _has_component_id(component, target_id) -> bool:
    if callable(component):
        component = component()
    if getattr(component, "id", None) == target_id:
        return True
    children = getattr(component, "children", None)
    if children is None:
        return False
    if not isinstance(children, (list, tuple)):
        children = [children]
    return any(_has_component_id(child, target_id) for child in children)


def test_status_message_assigns_level_class():
    message, class_name = _status_message("Saved preset", "success")

    assert message == "Saved preset"
    assert class_name == "status-banner status-banner--success"
    assert _status_class("idle") == "status-banner status-banner--idle"


def test_layout_contains_status_banner_and_download(tmp_path):
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))

    layout = app.layout() if callable(app.layout) else app.layout
    assert _has_component_id(layout, "status-banner")
    assert _has_component_id(layout, "export-download")
    assert any(isinstance(item, dcc.Download) for item in layout.children if hasattr(dcc, "Download"))
