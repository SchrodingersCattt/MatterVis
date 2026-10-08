"""Workbench placement must preserve native controls and callback dependencies."""

import json

import pytest
from dash import Input, Output, dcc, html

from _layout_helpers import find_component, layout_ids, walk_layout
from mat_viewer.app import create_app
from mat_viewer.app.layout_workbench import assemble_workbench
from mat_viewer.extensions import Extension


class WorkbenchProbe(Extension):
    name = "example.workbench"

    def build_web_panel(self, context):
        return html.Button("Extension", id="workbench-probe")

    def register_web(self, app, context):
        app.callback(Output("workbench-probe", "title"),
                     Input("workbench-probe", "n_clicks"))(lambda clicks: str(clicks))


@pytest.mark.parametrize("with_extension", [False, True])
def test_native_workbench_tree_and_dependencies(tmp_path, monkeypatch, with_extension):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    app = create_app(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path),
        extensions=(WorkbenchProbe(),) if with_extension else (),
    )
    try:
        assert app.config.update_title is None
        # Test the actual factory tree, including a fresh browser reload.
        for _ in range(2):
            root = app.layout()
            components = [node for node in walk_layout(root)
                          if isinstance(getattr(node, "id", None), str)]
            ids = [node.id for node in components]
            assert len(ids) == len(set(ids)), "native controls must not be duplicated"
            by_id = {node.id: node for node in components}
            top_ids = [getattr(node, "id", None) for node in root.children]
            columns = [cid for cid in top_ids if cid in {
                "left-panel", "center-panel", "right-panel", "mv-extension-panels",
            }]
            assert columns == ["left-panel", "center-panel"] + (
                ["mv-extension-panels"] if with_extension else []
            )
            assert "right-splitter" not in ids
            assert "right-panel" in layout_ids(by_id["left-panel"])
            assert "perf-log-panel" in layout_ids(by_id["center-panel"])
            assert "perf-log-panel" not in top_ids
            assert by_id["agent-state-poll"].interval == 500
            # Background figure requests and polling must not blank the current
            # scene through a Dash loading overlay.
            for node in walk_layout(root):
                if isinstance(node, dcc.Loading):
                    assert "crystal-graph" not in layout_ids(node)
            common = by_id["left-panel"].children[0]
            assert {"scene-tabs", "scene-cif-upload", "upload-status"} <= layout_ids(common)
            for tab in ("display", "analysis", "operation"):
                pane = by_id[f"{tab}-panel-content"]
                button = by_id[f"{tab}-panel-toggle"]
                props = button.to_plotly_json()["props"]
                assert pane.children, "inactive panes must be populated at startup"
                assert pane.role == "tabpanel"
                assert props["aria-controls"] == pane.id
                assert props["aria-selected"] == str(tab == "display").lower()
                assert props["aria-pressed"] == props["aria-selected"]
                assert props["tabIndex"] == (0 if tab == "display" else -1)
                assert ("analysis-tab-content--hidden" in pane.className) == (tab != "display")
            assert {"display-options", "view-projection", "save-preset-btn"} <= layout_ids(
                by_id["display-panel-content"]
            )
            assert "topology-site-index" in layout_ids(by_id["analysis-panel-content"])
            assert "transforms-add-btn" in layout_ids(by_id["operation-panel-content"])
            assert ("workbench-probe" in ids) is with_extension
            assert not app.config.suppress_callback_exceptions
            for callback in app.callback_map.values():
                dependencies = callback.get("inputs", []) + callback.get("state", [])
                outputs = callback["output"]
                dependencies = dependencies + [
                    {"id": output.component_id, "property": output.component_property}
                    for output in (outputs if isinstance(outputs, list) else [outputs])
                ]
                for dependency in dependencies:
                    cid = dependency["id"]
                    if isinstance(cid, str) and cid.startswith("{"):
                        cid = json.loads(cid)
                    if isinstance(cid, dict):  # Dynamic ALL/MATCH editor rows.
                        continue
                    if dependency.get("allow_optional") and cid not in by_id:
                        continue
                    assert cid in by_id, f"missing callback component: {cid}"
                    assert dependency["property"] in by_id[cid]._prop_names
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_assembly_moves_existing_components_without_replacing_them():
    root = html.Div([
        html.Div([html.Div(id="upload-status"), html.Button(id="display-control")],
                 id="left-panel"),
        html.Div([html.Div(id="crystal-graph")], id="center-panel"),
        html.Div([html.Div(id="analysis-control")], id="right-panel"),
        html.Div(id="perf-log-panel"),
        html.Div([html.Button(id="extension-input")], id="mv-extension-panels"),
    ])
    original = {node.id: node for node in walk_layout(root) if hasattr(node, "id")}
    result = assemble_workbench(root)
    assert result is root
    for cid, component in original.items():
        assert find_component(result, cid) is component