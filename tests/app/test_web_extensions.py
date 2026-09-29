"""Optional panels preserve the native Dash layout and backend."""

import pytest
from dash import Input, Output, html

from _layout_helpers import layout_ids
from mat_viewer.app import create_app
from mat_viewer.extensions import Extension


class WebProbe(Extension):
    name = "example.web"

    def __init__(self, fail=None, panel=True):
        self.events = []
        self.fail = fail
        self.panel = panel
        self.context = None

    def build_web_panel(self, context):
        self.context = context
        self.events.append("build")
        if self.fail == "build":
            raise RuntimeError("plugin build failed")
        if self.panel:
            return html.Button("probe", id="example-web-probe")

    def register_web(self, app, context):
        assert context is self.context
        assert context.viewer is app.crystal_backend
        self.events.append("register")
        if self.fail == "register":
            raise RuntimeError("plugin register failed")
        if self.panel:
            app.callback(Output("example-web-probe", "title"),
                         Input("example-web-probe", "n_clicks"))(
                lambda clicks: str(clicks)
            )

    def close(self):
        self.events.append("close")


def make_app(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    return create_app(preset_path=str(tmp_path / "preset.json"),
                      root_dir=str(tmp_path), **kwargs)


def test_no_extensions_preserves_native_layout(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    try:
        ids = layout_ids(app.layout)
        assert {"crystal-graph", "left-panel", "right-panel", "center-panel"} <= ids
        assert "mv-extension-panels" not in ids
    finally:
        app.close_extensions()
        app.crystal_backend.close()


@pytest.mark.parametrize("panel", [True, False])
def test_web_hooks_once_context_and_cleanup(tmp_path, monkeypatch, panel):
    plugin = WebProbe(panel=panel)
    app = make_app(tmp_path, monkeypatch, extensions=(plugin,))
    try:
        for _ in range(2):
            ids = layout_ids(app.layout)
            assert ("mv-extension-panels" in ids) is panel
            assert "crystal-graph" in ids
        assert plugin.events == ["build", "register"]
        assert app.extension_context is plugin.context
        state = app.crystal_backend.get_state()
        snapshot = plugin.context.snapshot()
        assert snapshot["structure_id"] == state.get("structure")
        assert snapshot["view_revision"] == state["render_revision"]
        if panel:
            assert "example-web-probe.title" in app.callback_map
    finally:
        app.close_extensions()
        app.close_extensions()
        app.crystal_backend.close()
    assert plugin.events.count("close") == 1


@pytest.mark.parametrize("fail", ["build", "register"])
def test_web_startup_failure_closes_plugins_and_backend(tmp_path, monkeypatch, fail):
    plugin = WebProbe(fail=fail)
    with pytest.raises(RuntimeError, match="plugin"):
        make_app(tmp_path, monkeypatch, extensions=(plugin,))
    assert plugin.events.count("close") == 1
    assert plugin.context.viewer._closed


def test_duplicate_rejected_before_backend_creation(monkeypatch):
    from mat_viewer.app import factory

    def forbidden(**kwargs):
        pytest.fail("must reject duplicate names before constructing a backend")

    monkeypatch.setattr(factory, "ViewerBackend", forbidden)
    with pytest.raises(ValueError, match="duplicate"):
        create_app(extensions=(WebProbe(), WebProbe()))