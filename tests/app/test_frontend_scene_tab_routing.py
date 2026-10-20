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


def test_scene_resync_runs_for_programmatic_selection_and_scene_cursor():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    select_idx = source.index("window.MatterVisSelectScene")
    select_body = source[select_idx:source.index("function fastViewMetadata", select_idx)]
    assert "resyncScene(sceneTabIntentId)" in select_body
    assert "scene_id=" in source


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


def test_visible_scene_chip_wins_over_hidden_selected_wrapper():
    """A stale hidden dcc.Tab must not fence frames for the visible chip."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const stop=source.indexOf('  // ── Right-click menu');
const prefix=source.slice(0,stop);
const hidden={id:'scene-tab-hidden'};
const chip={id:JSON.stringify({scene_id:'scene-chip'})};
const tabs={querySelector:(selector)=>selector.indexOf('.scene-tab-chip--selected')>=0?chip:hidden,dataset:{}};
const sandbox={console,Date,JSON,Number,String,Object,Array,Promise,setTimeout:()=>{},clearTimeout:()=>{},
  requestAnimationFrame:()=>{},MutationObserver:function(){this.observe=()=>{};},
  window:{Plotly:null,WebSocket:null,location:{protocol:'http:',host:'localhost'},
    dash_clientside:{set_props:()=>true}},
  document:{getElementById:id=>id==='scene-tabs'?tabs:null,querySelectorAll:()=>[],addEventListener:()=>{}}};
vm.createContext(sandbox);
vm.runInContext(prefix+'window.testScene={selectedSceneId,sceneIdFromTabNode};})();',sandbox);
assert.equal(sandbox.window.testScene.selectedSceneId(),'scene-chip');
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


def test_figure_socket_keeps_http_journal_polling_during_upgrade():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    ws_idx = source.index("(function connectWS()")
    ws_body = source[ws_idx:source.index("  function ensureSceneTabTooltips", ws_idx)]
    upgrade_idx = ws_body.index("ws = new WebSocket")
    assert "startHttpFallback();" in ws_body[:upgrade_idx]
    assert ws_body.index("startHttpFallback();") < upgrade_idx


def test_fallback_parked_frames_keep_revision_and_sequence_fences():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    ws_idx = source.index("(function connectWS()")
    ws_body = source[ws_idx:source.index("  function ensureSceneTabTooltips", ws_idx)]
    park_idx = ws_body.index("function parkFallbackFigure")
    park_body = ws_body[park_idx:ws_body.index("function deliverFallbackPayload", park_idx)]
    assert "rememberExpectedRender(payload, true)" in park_body
    assert "payload.isCurrent = function" in park_body
    assert "__mattervisFigureLastSeq" in park_body
    assert "previousSeq" in park_body


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


def test_scene_tab_scrolls_the_selected_tab_into_view():
    source = MATTERVIS_JS.read_text(encoding="utf-8")

    assert "function scrollSceneTabIntoView" in source
    assert "scrollIntoView({block: \"nearest\", inline: \"nearest\"})" in source
    assert "scheduleSceneTabScroll(clickedSceneId);" in source
    assert "scheduleSceneTabScroll(sceneId);" in source

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    start = source.index("  function sceneTabNodeForId")
    stop = source.index("  function selectedSceneId", start)
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const body=source.slice(%d,%d);
let calls=[];
const selected={id:'scene-tab-new_scene',scrollIntoView:options=>calls.push(options)};
const root={querySelectorAll:()=>[selected],querySelector:()=>selected};
const sandbox={String,document:{getElementById:id=>id==='scene-tabs'?root:null},
  window:{requestAnimationFrame:callback=>callback()},setTimeout};
vm.createContext(sandbox);
vm.runInContext(`
  function sceneIdFromTabNode(node) {
    if (!node || !node.id || node.id.indexOf('scene-tab-') !== 0) return null;
    if (node.id.indexOf('scene-tab-close-') === 0) return null;
    if (node.id.indexOf('scene-tab-label-') === 0) return node.id.slice('scene-tab-label-'.length);
    return node.id.slice('scene-tab-'.length);
  }
  ${body}
  scrollSceneTabIntoView('new_scene');
`,sandbox);
assert.equal(calls.length,1,'selected scene tab was not scrolled into view');
assert.equal(calls[0].block,'nearest');
assert.equal(calls[0].inline,'nearest');
""" % (start, stop)
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_scene_tab_mutations_scroll_only_after_selection_changes():
    """Tab class churn must not repeatedly scroll the active scene tab."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    stop = source.index("  // ── WS figure fast lane")
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const prefix=source.slice(0,%d);
let active='scene-a', calls=[], listeners={}, observerCallback=null;
const tabs={
  'scene-a':{id:'scene-tab-scene-a',querySelector:()=>null,scrollIntoView:options=>calls.push(['scene-a',options])},
  'scene-b':{id:'scene-tab-scene-b',querySelector:()=>null,scrollIntoView:options=>calls.push(['scene-b',options])},
};
const root={dataset:{},querySelector:()=>tabs[active],querySelectorAll:()=>Object.values(tabs),
  addEventListener:(kind,fn)=>{listeners[kind]=fn;}};
function FakeMutationObserver(callback){observerCallback=callback;}
FakeMutationObserver.prototype.observe=()=>{};
const sandbox={console,Date,JSON,Number,String,Object,Array,Promise,setTimeout:()=>{},clearTimeout:()=>{},
  requestAnimationFrame:callback=>callback(),MutationObserver:FakeMutationObserver,
  window:{requestAnimationFrame:callback=>callback(),Plotly:null,WebSocket:null,location:{protocol:'http:',host:'localhost'},
    dash_clientside:{set_props:()=>true}},
  document:{getElementById:id=>id==='scene-tabs'?root:null,querySelectorAll:()=>[],addEventListener:()=>{}}};
vm.createContext(sandbox);
vm.runInContext(prefix+`function ensureSceneTabTooltips(){} window.testScene={bindSceneTabIntent};})();`,sandbox);
sandbox.window.testScene.bindSceneTabIntent();
// Initial bind establishes a baseline but does not scroll.
assert.equal(calls.length,0);
// An unrelated class/aria mutation on the same active tab is a no-op.
observerCallback([]);
assert.equal(calls.length,0);
// Keyboard navigation changes the selected DOM tab and scrolls exactly once.
active='scene-b';
observerCallback([]);
assert.equal(calls.length,1);
assert.equal(calls[0][0],'scene-b');
observerCallback([]);
assert.equal(calls.length,1);
""" % stop
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_upload_tab_scroll_waits_for_new_tab_dom_node():
    """An upload must scroll the new tab after Dash paints it, not the old tab."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    stop = source.index("  // ── WS figure fast lane")
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const prefix=source.slice(0,%d);
let active='scene-a', calls=[], observerCallback=null;
const tabNodes={
  'scene-a':{id:'scene-tab-scene-a',scrollIntoView:options=>calls.push(['scene-a',options])},
};
const root={dataset:{},
  querySelector:()=>tabNodes[active] || null,
  querySelectorAll:()=>Object.values(tabNodes),
  addEventListener:()=>{},
};
function FakeMutationObserver(callback){observerCallback=callback;}
FakeMutationObserver.prototype.observe=()=>{};
const sandbox={console,Date,JSON,Number,String,Object,Array,Promise,
  setTimeout:()=>{},clearTimeout:()=>{},
  MutationObserver:FakeMutationObserver,
  window:{requestAnimationFrame:callback=>callback(),Plotly:null,WebSocket:null,
    location:{protocol:'http:',host:'localhost'},
    dash_clientside:{set_props:()=>true}},
  document:{getElementById:id=>id==='scene-tabs'?root:null,
    querySelectorAll:()=>[],addEventListener:()=>{}}};
vm.createContext(sandbox);
vm.runInContext(prefix+`function ensureSceneTabTooltips(){} window.testScene={bindSceneTabIntent};})();`,sandbox);
sandbox.window.testScene.bindSceneTabIntent();
// Upload completion selects the new scene before its tab is painted. The
// explicit request must not fall back to scrolling the old selected tab.
sandbox.window.MatterVisFlushSceneFrame('scene-b');
assert.equal(calls.length,0,'missing upload tab incorrectly scrolled old tab');
// Dash then inserts/selects the new tab; the observer retries and reaches it.
tabNodes['scene-b']={id:'scene-tab-scene-b',scrollIntoView:options=>calls.push(['scene-b',options])};
active='scene-b';
observerCallback([]);
assert.equal(calls.length,1,'new upload tab was not scrolled after it appeared');
assert.equal(calls[0][0],'scene-b');
assert.equal(calls[0][1].inline,'nearest');
// Attribute churn on the selected tab must not scroll it again.
observerCallback([]);
assert.equal(calls.length,1);
""" % stop
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


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


def test_failed_selected_scene_frame_is_parked_for_retry():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    handle_idx = source.index("function handleFigurePayload")
    handle_body = source[handle_idx:source.index("function canUseWebSocket", handle_idx)]

    assert "every failed fence" in handle_body
    assert "pendingFigureByScene[sceneKey] = p" in handle_body
    assert "replayQueuedSceneFigure(sceneKey, p)" in handle_body


def test_ws_figure_sequence_cursor_is_scene_scoped():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    handle_idx = source.index("function handleFigurePayload")
    handle_body = source[handle_idx:source.index("function canUseWebSocket", handle_idx)]

    assert "latestFigureSeqByScene[payloadScene]" in handle_body
    assert "window.__mattervisFigureLastSeq = Math.max" in handle_body


def test_http_figure_fallback_does_not_use_global_scene_cursor():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    poll_idx = source.index("function poll()")
    poll_body = source[poll_idx:source.index("function stopHttpFallback", poll_idx)]

    assert "var since = 0;" in poll_body
    assert "per-scene cursors" in poll_body


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


def test_upload_completion_flushes_frame_queued_before_tab_paint():
    """The upload barrier must flush a frame queued before Dash paints tabs."""
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    upload_source = (ROOT / "frontend" / "assets" / "native_upload.js").read_text(
        encoding="utf-8"
    )

    assert "window.MatterVisFlushSceneFrame" in source
    flush_idx = source.index("window.MatterVisFlushSceneFrame")
    flush_body = source[flush_idx : source.index("function flushPendingFigurePush", flush_idx)]
    assert "flushQueuedSceneFigure();" in flush_body
    assert "setTimeout(flushQueuedSceneFigure, delay)" in flush_body
    assert "window.MatterVisFlushSceneFrame(activeId)" in upload_source


def test_upload_flush_hook_replays_queued_hkust_frame():
    """Exercise the spinel -> HKUST selection barrier without a browser."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    start = source.index("  function scheduleQueuedSceneFigureRetry")
    stop = source.index("  function flushPendingFigurePush", start)
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const body=source.slice(%d,%d);
    const pending=Object.create(null);
    const sandbox={window:{},pending,setTimeout,clearTimeout};
vm.createContext(sandbox);
    vm.runInContext(`
      let current='spinel', applied=0;
      function currentSceneId(){return current;}
      function figurePushIsCurrent(frame){return frame.isCurrent();}
      function applyFigurePush(){applied += 1;}
      const pendingFigureRetryTimers=Object.create(null);
      window.MatterVisSelectScene=id=>{current=String(id);};
      const pendingFigureByScene=pending;
      ${body}
      pending.hkust={figure:{data:[{}],layout:{}},isCurrent:()=>current==='hkust'};
      window.MatterVisFlushSceneFrame('hkust');
      window.applied=applied;
    `,sandbox);
    assert.equal(sandbox.window.applied,1,'queued HKUST frame was not replayed after upload selection');
""" % (start, stop)
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_revision_zero_scene_frame_retries_after_selection_barrier():
    """A first frame for an unvisited scene must survive a late tab paint."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    start = source.index("  function scheduleQueuedSceneFigureRetry")
    stop = source.index("  function flushPendingFigurePush", start)
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const body=source.slice(%d,%d);
const timers=[];
const pending=Object.create(null);
const sandbox={window:{},pending,timers,setTimeout:(fn)=>{timers.push(fn);return timers.length;},clearTimeout:()=>{}};
vm.createContext(sandbox);
vm.runInContext(`
  let current='old', applied=0;
  function currentSceneId(){return current;}
  function figurePushIsCurrent(frame){return frame.isCurrent();}
  function applyFigurePush(){applied += 1;}
  const pendingFigureRetryTimers=Object.create(null);
  const pendingFigureByScene=pending;
  ${body}
  pending.new_scene={figure:{data:[{}],layout:{}},render_revision:0,isCurrent:()=>current==='new'};
  flushQueuedSceneFigure('new_scene');
  window.before=applied;
  current='new';
  while (timers.length) timers.shift()();
  window.after=applied;
`,sandbox);
assert.equal(sandbox.window.before,0,'frame painted before the new tab was selected');
assert.equal(sandbox.window.after,1,'revision-0 frame was dropped after the new tab painted');
""" % (start, stop)
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_direct_scene_frame_cancels_older_parked_retry():
    """A newer direct frame must not be overwritten by an older parked frame."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    handler_start = source.index("    function handleFigurePayload")
    handler_body = source[handler_start:source.index("    function canUseWebSocket", handler_start)]
    assert "clearQueuedSceneFigure(p.scene_id, seq);" in handler_body
    start = source.index("  function scheduleQueuedSceneFigureRetry")
    stop = source.index("  function flushPendingFigurePush", start)
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const body=source.slice(%d,%d);
const timers=[];
const cancelled=[];
const pending=Object.create(null);
const sandbox={window:{},pending,timers,cancelled,setTimeout:(fn)=>{timers.push(fn);return timers.length;},clearTimeout:(id)=>{cancelled.push(id);}};
vm.createContext(sandbox);
vm.runInContext(`
  function flushQueuedSceneFigure(){ }
  const pendingFigureRetryTimers=Object.create(null);
  const pendingFigureByScene=pending;
  ${body}
  pending.scene={figure:{data:[{}],layout:{}},figure_seq:10};
  scheduleQueuedSceneFigureRetry('scene',0);
  clearQueuedSceneFigure('scene',11);
  window.remaining=!!pending.scene;
  window.cancelled=cancelled.length;
`,sandbox);
assert.equal(sandbox.window.remaining,false,'older parked frame was retained after newer direct frame');
assert.equal(sandbox.window.cancelled,1,'parked-frame retry timer was not cancelled');
""" % (start, stop)
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_queued_scene_frame_replays_after_stale_dash_repaint():
    """Keep a parked frame long enough to defeat a delayed stale Dash paint."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    start = source.index("  function scheduleQueuedSceneFigureRetry")
    stop = source.index("  function flushPendingFigurePush", start)
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const body=source.slice(%d,%d);
const timers=[],pending=Object.create(null),gd={layout:{meta:{mattervis_render:{scene_id:'old',render_revision:0,display_version:0}}}};
const sandbox={window:{},pending,gd,timers,setTimeout:(fn)=>{timers.push(fn);return timers.length;},clearTimeout:()=>{}};
vm.createContext(sandbox);
vm.runInContext(`
  let applied=0;
  function graphDiv(){return gd;}
  function renderMetadataFromLayout(layout){return layout&&layout.meta&&layout.meta.mattervis_render||null;}
  function currentSceneId(){return 'new';}
  function figurePushIsCurrent(){return true;}
  function applyFigurePush(){applied += 1;}
  const pendingFigureRetryTimers=Object.create(null);
  const pendingFigureByScene=pending;
  ${body}
  const figure={data:[{}],layout:{meta:{mattervis_render:{scene_id:'new',render_revision:1,display_version:1}}}};
  pending.new={figure,figure_seq:10};
  flushQueuedSceneFigure('new');
  window.first=applied;
  timers.shift()();
  gd.layout=figure.layout;
  while (timers.length) timers.shift()();
  window.after=applied;
  window.remaining=!!pending.new;
`,sandbox);
assert.equal(sandbox.window.first,1,'initial queued frame was not applied');
assert.ok(sandbox.window.after >= 2,'stale Dash repaint did not trigger a replay');
assert.equal(sandbox.window.remaining,false,'settled queued frame was not released');
""" % (start, stop)
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_user_tab_click_flushes_frame_queued_before_selected_class_paint():
    """A click must replay a frame received while the previous tab is active."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const prefix=source.slice(0,source.indexOf('  // ── WS figure fast lane'));
const listeners={};
const graph={querySelector:()=>({})};
const tabs={dataset:{},querySelector:()=>({id:'scene-tab-old'}),addEventListener:(kind,fn)=>{listeners[kind]=fn;}};
let applied=0;
const sandbox={console,Date,JSON,Number,String,Object,Array,Promise,setTimeout,clearTimeout,
  requestAnimationFrame:()=>{},MutationObserver:function(){this.observe=()=>{};},
  window:{Plotly:{react:()=>{applied+=1;return Promise.resolve();}},WebSocket:null,
    location:{protocol:'http:',host:'localhost'},dash_clientside:{set_props:()=>true}},
  document:{getElementById:id=>id==='scene-tabs'?tabs:(id==='crystal-graph'?graph:null),
    querySelectorAll:()=>[],addEventListener:()=>{}}};
vm.createContext(sandbox);
vm.runInContext(prefix+`window.testScene={bindSceneTabIntent,pendingFigureByScene};})();`,sandbox);
const api=sandbox.window.testScene;
api.bindSceneTabIntent();
api.pendingFigureByScene.new_scene={figure:{data:[{}],layout:{}},isCurrent:()=>true};
listeners.click({target:{closest:()=>({id:'scene-tab-new_scene'})}});
assert.equal(applied,1,'queued frame was not replayed by the user tab click');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


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
