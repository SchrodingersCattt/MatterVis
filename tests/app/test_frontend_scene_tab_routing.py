from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from mat_viewer.app import create_app

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
    metadata_idx = source.index("fastViewMetadata()", current_idx)

    assert selected_idx < current_idx < metadata_idx
    current_body = source[current_idx:metadata_idx]
    assert "const tabScene = selectedSceneId();" in current_body
    assert "if (tabScene) return tabScene;" in current_body


def test_scene_tab_intent_is_bound_by_global_rebind_observer():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    rebind_idx = source.index("function rebindAll")
    rebind_body = source[rebind_idx:source.index("new MutationObserver", rebind_idx)]

    assert "bindSceneTabIntent();" in rebind_body
    assert "ensureSceneTabTooltips();" in rebind_body


def test_programmatic_upload_selection_clears_stale_tab_intent():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    selected_idx = source.index("function selectedSceneId")
    selected_body = source[selected_idx:source.index("function fastViewMetadata", selected_idx)]
    assert "const domSceneId" in selected_body
    assert "sceneTabIntentId = null" in selected_body
    assert "return domSceneId" in selected_body
    assert "window.MatterVisSelectScene" in source


def test_nested_tab_label_and_programmatic_upload_use_real_scene_id():
    """A click on the label must route frames by the enclosing scene id."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const stop=source.indexOf('  // ── Right-click menu');
const prefix=source.slice(0,stop);
let selected={id:'scene-tab-old'};
const tabs={querySelector:()=>selected,dataset:{}};
const sandbox={console,Date,JSON,Number,String,Object,Array,Promise,setTimeout:()=>{},clearTimeout:()=>{},
  requestAnimationFrame:()=>{},MutationObserver:function(){this.observe=()=>{};},
  window:{Plotly:null,WebSocket:null,location:{protocol:'http:',host:'localhost'},
    dash_clientside:{set_props:()=>true}},
  document:{getElementById:id=>id==='scene-tabs'?tabs:null,querySelectorAll:()=>[],
    addEventListener:()=>{}}};
vm.createContext(sandbox);
vm.runInContext(prefix+'window.testScene={selectedSceneId,noteSceneTabIntent,sceneIdFromTabNode};})();',sandbox);
const api=sandbox.window.testScene;
api.noteSceneTabIntent({closest:()=>({id:'scene-tab-label-new'})});
assert.equal(api.selectedSceneId(),'new');
api.selectedSceneId();
sandbox.window.MatterVisSelectScene('uploaded');
assert.equal(api.selectedSceneId(),'uploaded');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_failed_figure_socket_switches_to_one_http_fallback():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    ws_idx = source.index("(function connectWS()")
    ws_body = source[ws_idx:source.index("  function ensureSceneTabTooltips", ws_idx)]
    assert "__mattervisFigureFallbackActive" in ws_body
    assert "__mattervisFigureWsDisabled" in ws_body
    assert "Invalid frame header" in ws_body
    assert "setTimeout(connectWS,5000)" in ws_body


def test_tab_switch_clears_global_upload_status():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    start = source.index("function bindSceneTabIntent")
    body = source[start:source.index("// ── WS figure fast lane", start)]
    assert 'document.getElementById("upload-status")' in body
    assert 'status.textContent = ""' in body


def test_scene_tab_tooltip_is_applied_to_actual_tab_node():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    assert "function ensureSceneTabTooltips" in source
    tooltip_idx = source.index("function ensureSceneTabTooltips")
    tooltip_body = source[tooltip_idx:source.index("function rebindAll", tooltip_idx)]
    assert 'tab.setAttribute("title", fullName)' in tooltip_body
    assert 'tab.setAttribute("aria-label", fullName)' in tooltip_body


def test_figure_socket_is_singleton_across_asset_rebinds():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    ws_idx = source.index("(function connectWS()")
    ws_body = source[ws_idx:source.index("})();", ws_idx) + 4]
    assert "__mattervisFigureSocket" in ws_body
    assert "window.__mattervisFigureSocket = null" in ws_body


def test_deferred_ws_figure_keeps_and_revalidates_render_metadata():
    source = MATTERVIS_JS.read_text(encoding="utf-8")

    assert "function currentRenderRevision" in source
    assert "function figurePushIsCurrent" in source
    assert "pendingFigurePush=p" in source
    flush_idx = source.index("function flushPendingFigurePush")
    flush_body = source[flush_idx:source.index("function markActive", flush_idx)]
    assert "figurePushIsCurrent(p)" in flush_body


def test_upload_frame_is_queued_until_new_scene_tab_is_selected():
    source = MATTERVIS_JS.read_text(encoding="utf-8")

    assert "pendingFigureByScene" in source
    assert "flushQueuedSceneFigure" in source
    rebind_idx = source.index("function rebindAll")
    rebind_body = source[rebind_idx:source.index("new MutationObserver", rebind_idx)]
    assert "flushQueuedSceneFigure();" in rebind_body
    ws_idx = source.index("if (!isCurrentFigurePush(p))")
    ws_body = source[ws_idx:source.index("if (interactionActive)", ws_idx)]
    assert "pendingFigureByScene[String(p.scene_id)] = p;" in ws_body


def test_plotly_apply_gate_rejects_stale_dash_figures_and_patches():
    source = MATTERVIS_JS.read_text(encoding="utf-8")

    assert "function installPlotlyRenderGate" in source
    assert "function figureMetadataIsCurrent" in source
    assert 'wrap("newPlot")' in source
    assert 'wrap("react")' in source
    assert 'wrap("update")' in source
    assert 'wrap("relayout")' in source
    assert 'wrap("restyle")' in source
    assert "mattervis_render" in source


def test_revision_lookup_never_combines_clicked_scene_with_other_scene_metadata():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    revision_idx = source.index("function currentRenderRevision")
    revision_body = source[revision_idx:source.index("function isCurrentFigurePush", revision_idx)]

    assert "m.scene_id" in revision_body
    assert "currentSceneId()" in revision_body


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
