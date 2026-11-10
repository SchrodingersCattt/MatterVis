"""Scene tabs must build on the supported Dash range, not only the CI pin.

Dash 2.18.2 through 3.x reject a component in ``dcc.Tab.label`` while the
layout is constructed. That exception used to be caught and replaced with
the error screen. Dash 4.0 and later accept the component. The layout
follows ``Tab._base_nodes`` so both contracts serve the viewer.
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI
    import tomli as tomllib

import pytest
from dash.development.base_component import Component

from mat_viewer.app import ViewerBackend, create_app
from mat_viewer.app.dash_tabs import tab_label_accepts_components


def _has_component_id(component, target_id) -> bool:
    if getattr(component, "id", None) == target_id:
        return True
    children = getattr(component, "children", None)
    label = getattr(component, "label", None)
    if label is not None and not isinstance(label, str):
        children = [label] if children is None else [children, label]
    if children is None:
        return False
    if not isinstance(children, (list, tuple)):
        children = [children]
    return any(
        _has_component_id(child, target_id)
        for child in children
        if not isinstance(child, str)
    )


ROOT = Path(__file__).resolve().parents[2]


def _close_id(scene_id: str) -> str:
    return f"scene-tab-close-{scene_id}"


def test_web_extra_declares_the_dash_floor() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    for name in ("web", "all"):
        dash_reqs = [item for item in extras[name] if item == "dash" or item.startswith("dash")]
        assert dash_reqs == ["dash>=2.18.2"]


def test_installed_label_contract_keeps_selection_and_one_close_control(tmp_path: Path) -> None:
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        scene_id = backend.active_scene_id()
        label_text = next(item["label"] for item in backend.scene_options() if item["id"] == scene_id)
        tabs = backend.scene_tabs()
        row = backend.scene_tab_close_row()
        tab = next(item for item in tabs if item.value == scene_id)
        assert tab.id == f"scene-tab-{scene_id}"
        close_id = _close_id(scene_id)
        if tab_label_accepts_components():
            assert isinstance(tab.label, Component)
            assert _has_component_id(tab, close_id)
            assert _has_component_id(tab, f"scene-tab-label-{scene_id}")
            assert row == []
        else:
            assert tab.label == str(label_text)
            assert not _has_component_id(tab, close_id)
            assert any(_has_component_id(child, close_id) for child in row)
    finally:
        backend.close(wait=True)


def test_string_label_fallback_keeps_close_outside_the_tab(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "mat_viewer.app.dash_tabs.tab_label_accepts_components",
        lambda: False,
    )
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        scene_id = backend.active_scene_id()
        tabs = backend.scene_tabs()
        row = backend.scene_tab_close_row()
        tab = next(item for item in tabs if item.value == scene_id)
        close_id = _close_id(scene_id)
        assert isinstance(tab.label, str)
        assert tab.label
        assert not _has_component_id(tab, close_id)
        assert any(_has_component_id(child, close_id) for child in row)
        # A string cannot trip the Dash constructor gate that rejects components.
        assert not isinstance(tab.label, Component)
    finally:
        backend.close(wait=True)


def test_layout_endpoint_serves_the_viewer(tmp_path: Path) -> None:
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        response = app.server.test_client().get("/_dash-layout")
        body = response.get_data(as_text=True)
        assert response.status_code == 200, body[:500]
        assert "Layout generation failed" not in body
        assert "crystal-graph" in body
        assert '"scene-tabs"' in body or '"id": "scene-tabs"' in body or "scene-tabs" in body
        scene_id = app.crystal_backend.active_scene_id()
        close_id = _close_id(scene_id)
        assert body.count(close_id) == 1
        if tab_label_accepts_components():
            assert "scene-tabs-card--string-labels" not in body
            assert f"scene-tab-label-{scene_id}" in body
        else:
            assert "scene-tabs-card--string-labels" in body
            assert f"scene-tab-label-{scene_id}" not in body
    finally:
        app.crystal_backend.close(wait=True)


def test_string_label_layout_is_still_the_viewer(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "mat_viewer.app.dash_tabs.tab_label_accepts_components",
        lambda: False,
    )
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        response = app.server.test_client().get("/_dash-layout")
        body = response.get_data(as_text=True)
        assert response.status_code == 200, body[:500]
        assert "Layout generation failed" not in body
        assert "crystal-graph" in body
        assert "scene-tabs-card--string-labels" in body
        scene_id = app.crystal_backend.active_scene_id()
        assert body.count(_close_id(scene_id)) == 1
        assert f"scene-tab-label-{scene_id}" not in body
        assert f"scene-tab-{scene_id}" in body
    finally:
        app.crystal_backend.close(wait=True)


def test_component_label_is_rejected_by_string_only_dash() -> None:
    """Document the constructor gate the fallback exists to avoid.

    On a Dash that already accepts component labels this is a no-op check
    of the base-node list. On Dash 2.18.2 through 3.x it is the failure
    that used to replace the viewer.
    """
    from dash import dcc, html

    accepts = tab_label_accepts_components()
    if accepts:
        assert "label" in dcc.Tab._base_nodes
        return
    with pytest.raises(TypeError, match="label"):
        dcc.Tab(label=html.Span("scene"), value="scene", id="scene-tab-scene")
