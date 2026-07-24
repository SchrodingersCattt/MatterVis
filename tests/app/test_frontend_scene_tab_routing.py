from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import create_app

from _layout_helpers import find_component


ROOT = Path(__file__).resolve().parents[2]
MATTERVIS_JS = ROOT / "frontend" / "assets" / "mattervis.js"


def test_ws_figure_routing_prefers_clicked_scene_tab_over_fast_metadata():
    """Regression for tab switches applying the previous scene's figure.

    The hidden ``fast-view-metadata`` store is updated by Dash callbacks and can
    lag behind an in-flight tab click.  The WebSocket figure fast lane must
    therefore route incoming figures by the user's current tab intent/selection
    before falling back to ``fast-view-metadata.scene_id``.  Otherwise the
    correct figure for the clicked tab is dropped as "not current" and the user
    must hard-refresh to recover.
    """
    source = MATTERVIS_JS.read_text(encoding="utf-8")

    assert "sceneTabIntentId" in source
    assert "function noteSceneTabIntent" in source
    assert "function selectedSceneId" in source
    assert "function currentSceneId" in source

    selected_idx = source.index("function selectedSceneId")
    current_idx = source.index("function currentSceneId")
    metadata_idx = source.index('document.getElementById("fast-view-metadata")', current_idx)

    assert selected_idx < current_idx < metadata_idx
    current_body = source[current_idx:metadata_idx]
    assert "const tabScene = selectedSceneId();" in current_body
    assert "if (tabScene) return tabScene;" in current_body


def test_scene_tab_intent_is_bound_by_global_rebind_observer():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    rebind_idx = source.index("function rebindAll")
    rebind_body = source[rebind_idx:source.index("new MutationObserver", rebind_idx)]

    assert "bindSceneTabIntent();" in rebind_body


def test_main_graph_disables_plotly_double_click_reset(tmp_path):
    """Double-clicking empty graph space must not reset the camera.

    Plotly's default double-click behavior relayouts the 3D scene back to an
    autorange/default camera. The SVG compass reads the live WebGL camera, so a
    double-click looked like the compass suddenly changed even though the user
    did not press MatterVis' explicit Reset button.
    """
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    graph = find_component(app.layout, "crystal-graph")

    assert graph is not None
    assert graph.config.get("doubleClick") is False
