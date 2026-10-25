"""Node regressions for the tracker rows that do not need a GPU."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "frontend" / "assets"


def _node(program: str, *sources: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    result = subprocess.run(
        [node, "-e", program, *[str(path) for path in sources]],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (result.stderr or result.stdout)[-4000:]


def test_out_of_order_versions_and_remount():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const calls = [];
let gd = {layout:{meta:{mattervis_render:{scene_id:'s', geometry_version:1, display_version:1, camera_version:1, scene_generation:'1'}}}, data:[{meta:{mv_role:'labels'}, x:[1], y:[1], z:[1]}]};
const sandbox = {console, Promise, Number, String, Object, Array, JSON, window:{
  MatterVisViewCamera:{graph:()=>gd, applyAsync:()=>Promise.resolve(true)},
  Plotly:{restyle:()=>Promise.resolve(), react:(target)=> {calls.push(target); return Promise.resolve();}},
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
(async () => {
  const third = await submit({type:'view_update', update_kind:'display', scene_id:'s', geometry_version:1, display_version:3, camera_version:1, payload:{display_options:['labels']}});
  assert.equal(third, 'display');
  const older = submit({type:'view_update', update_kind:'display', scene_id:'s', geometry_version:1, display_version:2, camera_version:1, payload:{display_options:[]}});
  assert.equal(older, 'stale');
  gd = {layout:{meta:{mattervis_render:{scene_id:'s', geometry_version:1, display_version:1, camera_version:1, scene_generation:'1'}}}, data:[{meta:{mv_role:'labels'}, x:[1], y:[1], z:[1]}]};
  const remounted = await submit({type:'figure', scene_id:'s', update_kind:'geometry', geometry_version:1, display_version:1, camera_version:1, operation_id:'remount', figure:{data:[{type:'scatter3d', x:[0], y:[0], z:[0]}], layout:{scene:{}}}});
  assert.equal(remounted, 'figure');
  assert.equal(calls.length, 1);
  const updates = [];
  for (let i = 0; i < 40; i += 1) updates.push({type:'figure', figure_seq:i+1, figure:{data:[{type:'scatter3d'}]}});
  updates.push({type:'render_error', error:'dropped'});
  const latest = sandbox.window.MatterVisViewUpdates.pickLatest(updates);
  assert.equal(latest.type, 'render_error');
  assert.equal(latest.figure_seq, undefined);
  const figure = sandbox.window.MatterVisViewUpdates.pickLatest(updates.slice(0, 40));
  assert.equal(figure.figure_seq, 40);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "view_updates.js",
    )


def test_camera_noop_rejection_and_webgl_loss_restore_the_view():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
const gd = {
  data:[{type:'scatter3d', x:[1], y:[1], z:[1]}],
  layout:{scene:{camera:{eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'perspective'}}}},
  _fullLayout:{scene:{_scene:{camera:{_ortho:false, eye:[1,1,1], center:[0,0,0], up:[0,0,1]}, glplot:{canvas:{addEventListener:(type, fn)=>{listeners[type]=fn;}}}}}},
  addEventListener(){},
  querySelector(){ return null; },
};
let mode = 'reject';
const sandbox = {console, Promise, Number, String, Object, Array, JSON, Date, setTimeout, clearTimeout, Math, window:{
  document:{getElementById:(id)=> id==='crystal-graph'?{querySelector:()=>gd}: id==='mv-render-status'?status:null},
  Plotly:{
    relayout:(_gd, patch)=>{
      if (mode === 'reject') return Promise.reject(new Error('relayout failed'));
      if (mode === 'noop') return Promise.resolve();
      const eye = patch['scene.camera.eye'];
      const projection = patch['scene.camera.projection.type'];
      gd.layout.scene.camera.eye = eye || gd.layout.scene.camera.eye;
      if (projection) gd.layout.scene.camera.projection = {type: projection};
      gd._fullLayout.scene._scene.camera._ortho = projection === 'orthographic';
      if (eye) gd._fullLayout.scene._scene.camera.eye = [eye.x, eye.y, eye.z];
      return Promise.resolve();
    },
    react:(_gd, _data, layout)=>{
      gd.layout = layout;
      const camera = layout.scene.camera;
      gd._fullLayout.scene._scene.camera._ortho = camera.projection.type === 'orthographic';
      gd._fullLayout.scene._scene.camera.eye = [camera.eye.x, camera.eye.y, camera.eye.z];
      return Promise.resolve();
    },
  },
}};
const status = {hidden:true, textContent:'', setAttribute(name, value){ this[name]=value; }};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const cameraApi = sandbox.window.MatterVisViewCamera;
const camera = {eye:{x:1.2,y:0.2,z:0.4}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}};
(async () => {
  mode = 'reject';
  assert.equal(await cameraApi.applyAsync(camera, {camera_version:1, command:'projection'}), true);
  assert.equal(gd.layout.scene.camera.projection.type, 'orthographic');
  assert.equal(sandbox.window.__mvRenderStatus.level, 'recovered');
  mode = 'noop';
  gd._fullLayout.scene._scene.camera._ortho = false;
  assert.equal(await cameraApi.applyAsync(camera, {camera_version:2, command:'projection'}), true);
  assert.equal(gd._fullLayout.scene._scene.camera._ortho, true);
  mode = 'apply';
  const drag = cameraApi.applyAsync({eye:{x:4,y:4,z:4}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}}, {camera_version:3});
  await Promise.resolve();
  let releaseDrag;
  const held = new Promise((resolve) => { releaseDrag = resolve; });
  const original = sandbox.window.Plotly.relayout;
  sandbox.window.Plotly.relayout = (target, patch) => held.then(() => original(target, patch));
  const reset = cameraApi.applyAsync(camera, {camera_version:4, command:'reset'});
  await Promise.resolve(); await Promise.resolve();
  sandbox.window.Plotly.relayout = original;
  releaseDrag();
  await assert.rejects(drag, /stale|timeout/);
  assert.equal(await reset, true);
  const eye = gd.layout.scene.camera.eye;
  assert.ok(Math.hypot(eye.x, eye.y, eye.z) < 2);
  listeners.webglcontextlost({preventDefault(){ this.prevented = true; }, prevented:false});
  assert.equal(gd.layout.scene.camera.projection.type, 'orthographic');
  assert.match(sandbox.window.__mvRenderStatus.message, /WebGL context lost/);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "view_camera.js",
    )


def test_render_failure_status_and_retry_without_reload():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {layout:{meta:{mattervis_render:{scene_id:'s', geometry_version:1, display_version:1, camera_version:1}}}, data:[{meta:{mv_role:'atom'}, x:[1], y:[1], z:[1]}]};
let fail = true;
const sandbox = {console, Promise, Number, String, Object, Array, JSON, Date, window:{
  MatterVisViewCamera:{graph:()=>gd, reportStatus:(level, message)=>{ sandbox.window.__mvRenderStatus = {level, message}; }},
  Plotly:{react:()=> fail ? Promise.reject(new Error('react failed')) : Promise.resolve(), restyle:()=>Promise.reject(new Error('restyle failed'))},
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
(async () => {
  const error = submit({type:'render_error', scene_id:'s', error:'worker failed'});
  assert.equal(error, 'error');
  assert.match(sandbox.window.__mvRenderStatus.message, /worker failed/);
  await assert.rejects(submit({type:'figure', scene_id:'s', update_kind:'geometry', geometry_version:2, display_version:1, camera_version:1, figure:{data:[{type:'scatter3d', x:[1], y:[1], z:[1]}], layout:{scene:{}}}}));
  fail = false;
  const retried = await submit({type:'figure', scene_id:'s', update_kind:'geometry', geometry_version:2, display_version:1, camera_version:1, operation_id:'retry', figure:{data:[{type:'scatter3d', x:[1], y:[1], z:[1]}], layout:{scene:{}}}});
  assert.equal(retried, 'figure');
  await assert.rejects(submit({type:'view_update', update_kind:'display', scene_id:'s', geometry_version:2, display_version:4, camera_version:1, payload:{display_options:[], opacity:0.4}}));
  assert.match(sandbox.window.__mvRenderStatus.message, /display patch failed/);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "view_updates.js",
    )


def test_label_restyle_is_one_batch_and_disorder_preview_drops_a_stale_scene():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const updates = fs.readFileSync(process.argv[1], 'utf8');
const preview = fs.readFileSync(process.argv[2], 'utf8');
const restyles = [];
const added = [];
const gd = {on(){}, __mvGraphGeneration:1, layout:{meta:{mattervis_render:{scene_id:'scene-a'}}}, data:[
  {meta:{mv_role:'labels'}, name:'a'}, {meta:{mv_role:'labels'}, name:'b'}, {meta:{kind:'polyhedron', spec_id:'p'}, name:'poly'},
]};
const sandbox = {console, Promise, Number, String, Object, Array, JSON, window:{
  MatterVisViewCamera:{graph:()=>gd},
  MatterVisRenderQueue:{enqueue(_gd, task){ return Promise.resolve().then(task); }},
  Plotly:{
    restyle:(_gd, patch, indices)=>{ restyles.push({patch, indices}); return Promise.resolve(); },
    deleteTraces:()=>Promise.resolve(),
    addTraces:(_gd, traces)=>{ added.push(traces); return Promise.resolve(); },
  },
  document:{querySelector:()=>gd, getElementById:()=>gd},
  requestAnimationFrame:(fn)=>fn(),
}};
vm.createContext(sandbox);
vm.runInContext(updates, sandbox);
vm.runInContext(preview, sandbox);
(async () => {
  const result = await sandbox.window.MatterVisViewUpdates.submit({
    type:'view_update', update_kind:'display', scene_id:'scene-a', geometry_version:1, display_version:2, camera_version:1,
    payload:{display_options:[], polyhedron_specs:[{id:'p', enabled:false}]},
  });
  assert.equal(result, 'display');
  assert.equal(restyles.length, 1);
  assert.equal(JSON.stringify(restyles[0].patch.visible.map(Boolean)), '[false,false,false]');
  sandbox.window.MatterVisDisorderPreview.prepare({replicas:[{id:'r', preview_mesh:{x:[1], y:[1], z:[1], i:[0], j:[0], k:[0]}}]});
  gd.layout.meta.mattervis_render.scene_id = 'scene-b';
  gd.__mvGraphGeneration = 2;
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(added.length, 0);
  gd.layout.meta.mattervis_render.scene_id = 'scene-a';
  gd.__mvGraphGeneration = 1;
  sandbox.window.MatterVisDisorderPreview.prepare({replicas:[{id:'r', preview_mesh:{x:[1], y:[1], z:[1], i:[0], j:[0], k:[0]}}]});
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(added.length, 1);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "view_updates.js",
        ASSETS / "disorder_preview.js",
    )


def test_polyhedron_row_updates_respect_the_global_overlay_gate():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {layout:{meta:{mattervis_render:{scene_id:'scene-a'}}}, data:[
  {meta:{kind:'polyhedron', spec_id:'enabled'}, visible:true},
  {meta:{kind:'polyhedron', spec_id:'disabled'}, visible:false},
  {meta:{mv_role:'atom'}, visible:true},
]};
const sandbox = {console, Promise, Number, String, Object, Array, JSON, window:{
  MatterVisViewCamera:{graph:()=>gd},
  Plotly:{restyle:(_gd, patch, indices)=>{
    indices.forEach((index, offset)=>{ gd.data[index].visible = patch.visible[offset]; });
    return Promise.resolve();
  }},
}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
  const specs = [{id:'enabled', enabled:true}, {id:'disabled', enabled:false}];
  const apply = async (topology, revision) => {
    assert.equal(await sandbox.window.MatterVisViewUpdates.submit({
      type:'view_update', update_kind:'display', scene_id:'scene-a',
      geometry_version:1, display_version:revision,
      payload:{topology_enabled:topology, polyhedron_specs:specs},
    }), 'display');
  };
  await apply(false, 1);
  assert.deepEqual(gd.data.map(t=>t.visible), [false,false,true]);
  specs[1].enabled = true;
  await apply(false, 2);
  assert.deepEqual(gd.data.map(t=>t.visible), [false,false,true]);
  specs[1].enabled = false;
  await apply(true, 3);
  assert.deepEqual(gd.data.map(t=>t.visible), [true,false,true]);
  assert.deepEqual(specs.map(s=>s.enabled), [true,false]);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "view_updates.js",
    )


def test_splitter_drag_resizes_only_when_the_graph_rect_changes():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = {};
function node(id) {
  return {id, dataset:{}, style:{}, children:[], classList:{add(){}, remove(){}, toggle(){}, contains(){return false;}}, appendChild(child){this.children.push(child);}, getBoundingClientRect(){return this.rect;}, get clientWidth(){return Math.round((this.rect && this.rect.width) || 0);}, get clientHeight(){return Math.round((this.rect && this.rect.height) || 0);}, setAttribute(){}, getAttribute(){return null;}, addEventListener(type, fn){listeners[id+':'+type]=fn;}, querySelector:(sel)=> sel==='.js-plotly-plot'?plot:null, classListContains:false};
}
const plot = {on(){}, style:{}, classList:{contains:()=>false}};
const graph = node('crystal-graph');
graph.rect = {width:800, height:600, left:200, right:1000, top:0, bottom:600};
const left = node('left-panel');
left.rect = {width:280, height:600, left:0, right:280, top:0, bottom:600};
const root = node('viewer-root');
root.rect = {width:1400, height:600, left:0, right:1400, top:0, bottom:600};
const splitter = node('left-splitter');
const ids = {'crystal-graph':graph, 'left-panel':left, 'viewer-root':root, 'left-splitter':splitter, 'mv-extension-panels':null};
let resizes = 0, windowResizes = 0;
const sandbox = {console, window:{
  requestAnimationFrame:(fn)=>{ fn(); return 1; },
  cancelAnimationFrame(){},
  addEventListener(type, fn){ listeners['window:'+type]=fn; if (type==='resize') windowResizes += 0; },
  removeEventListener(){},
  dispatchEvent(event){ if (event && event.type==='resize') windowResizes += 1; },
  Plotly:{Plots:{resize(){ resizes += 1; }}, relayout(){ resizes += 1; }},
  document:{
    readyState:'complete',
    body:{dataset:{}, classList:{add(){}, remove(){}}, appendChild(){}},
    documentElement:{},
    getElementById:(id)=>ids[id]||null,
    querySelector:()=>null,
    querySelectorAll:()=>[{}],
    addEventListener(){},
    createElement:()=>({className:'', type:'', id:'', dataset:{}, style:{}, setAttribute(){}, addEventListener(){}, appendChild(){}}),
  },
}, document:null, MutationObserver:class{observe(){} disconnect(){}}, Event:class{constructor(type){this.type=type;}}};
sandbox.document = sandbox.window.document;
sandbox.window.Event = sandbox.Event;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
windowResizes = 0;
const before = resizes;
assert.equal(typeof listeners['left-splitter:mousedown'], 'function');
listeners['left-splitter:mousedown']({preventDefault(){}, clientX:300});
const move = listeners['window:mousemove'];
if (typeof move !== 'function') throw new Error('listeners ' + Object.keys(listeners).join(','));
graph.rect = {width:760, height:600, left:240, right:1000, top:0, bottom:600};
move({clientX:340});
move({clientX:360});
assert.equal(resizes, before + 1);
const afterSame = resizes;
move({clientX:380});
assert.equal(resizes, afterSame);
assert.equal(windowResizes, 0);
""",
        ASSETS / "panel_resize.js",
    )


def test_shortcuts_ignore_typing_targets_and_missing_on_does_not_throw():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const mattervis = fs.readFileSync(process.argv[1], 'utf8');
class Target {
  constructor() { this.listeners = {}; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener() {}
  dispatchEvent(event) { (this.listeners[event.type] || []).forEach((fn) => fn(event)); }
}
class El extends Target {
  constructor(tag) { super(); this.tagName = tag; this.children = []; this.style = {}; this.dataset = {}; this.classList = {add(){}, remove(){}, contains(){return false;}, toggle(){}}; this.id = ''; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) { const out = []; const walk = (node) => { if (matches(node, sel)) out.push(node); (node.children||[]).forEach(walk); }; (this.children||[]).forEach(walk); return out; }
  getBoundingClientRect() { return {width:800, height:600, left:0, top:0, right:800, bottom:600}; }
  contains(node) { return this === node || (this.children||[]).some((child) => child.contains && child.contains(node)); }
  setAttribute(name, value) { this[name] = value; }
  closest() { return null; }
}
function matches(node, sel) {
  if (!node || !node.tagName) return false;
  if (sel === '.js-plotly-plot') return node.className === 'js-plotly-plot';
  if (sel.startsWith('#')) return node.id === sel.slice(1);
  return node.tagName.toLowerCase() === sel.toLowerCase();
}
const document = new El('document');
document.readyState = 'complete';
document.body = new El('body');
document.documentElement = new El('html');
document.head = new El('head');
document.body.appendChild(document.documentElement);
const viewer = new El('div'); viewer.id = 'viewer-root';
const graph = new El('div'); graph.id = 'crystal-graph';
const plot = new El('div'); plot.className = 'js-plotly-plot'; plot.on = 'not-a-function';
plot.layout = {annotations:[{name:'mv_compass', text:'a'}], shapes:[{name:'mv_compass'}], meta:{}};
graph.appendChild(plot);
viewer.appendChild(graph);
const help = new El('div'); help.id = 'kbd-help'; help.classList = {names:new Set(['kbd-help--hidden']), contains(name){return this.names.has(name);}, add(name){this.names.add(name);}, remove(name){this.names.delete(name);}, toggle(){}};
document.body.appendChild(viewer);
document.body.appendChild(help);
document.getElementById = (id) => document.querySelector('#'+id);
document.querySelector = (sel) => document.body.querySelector(sel);
document.querySelectorAll = (sel) => document.body.querySelectorAll(sel);
document.createElement = (tag) => new El(tag);
const stores = [];
const errors = [];
const window = {
  document, MATTERVIS_WS_FIGURE:false,
  addEventListener(type, fn){ document.addEventListener(type, fn); },
  removeEventListener(){},
  requestAnimationFrame:(fn)=>0,
  setTimeout:(fn)=>0,
  clearTimeout(){},
  location:{protocol:'http:', host:'localhost'},
  dash_clientside:{set_props(id, props){ stores.push({id, data:props.data}); }},
  Plotly:{relayout(gd, update){ gd.layout.annotations = update.annotations; gd.layout.shapes = update.shapes; return {finally(fn){fn();}}; }},
};
window.window = window;
const sandbox = {console, window, document, requestAnimationFrame:(fn)=>0, setTimeout:(fn)=>0, clearTimeout(){}, cancelAnimationFrame(){}, MutationObserver:class{constructor(){} observe(){} disconnect(){}}, WebSocket:class{constructor(){ throw new Error('socket'); }}};
vm.createContext(sandbox);
try { vm.runInContext(mattervis, sandbox); }
catch (error) { errors.push(String(error && error.stack || error)); }
if (errors.length) throw new Error(errors.join('\n'));
plot.layout.annotations = [{name:'mv_compass', text:'again'}];
plot.layout.shapes = [{name:'mv_compass'}];
sandbox.window.MatterVisCompass.strip(plot);
assert.equal(plot.layout.annotations.length, 0);
assert.equal(plot.layout.shapes.length, 0);
plot.layout.annotations = [{name:'mv_compass', text:'reinjected'}];
sandbox.window.MatterVisCompass.strip(plot);
assert.equal(plot.layout.annotations.length, 0);
function press(key, target, extra) {
  const event = Object.assign({type:'keydown', key, target, ctrlKey:false, metaKey:false, altKey:false, shiftKey:false, prevented:false, preventDefault(){this.prevented=true;}}, extra||{});
  document.dispatchEvent(event);
  return event;
}
const before = stores.length;
const typed = press('h', {tagName:'INPUT', isContentEditable:false});
assert.equal(typed.prevented, false);
assert.equal(stores.length, before);
const handled = press('h', {tagName:'DIV', isContentEditable:false});
assert.equal(handled.prevented, true);
assert.equal(stores.at(-1).data.action, 'hide');
const selectAll = press('a', {tagName:'DIV'}, {ctrlKey:true});
assert.equal(selectAll.prevented, true);
assert.equal(stores.at(-1).data.action, 'select_all');
const typedSelect = press('a', {tagName:'TEXTAREA'}, {ctrlKey:true});
assert.equal(typedSelect.prevented, false);
""",
        ASSETS / "mattervis.js",
    )


def test_long_run_delivery_map_stays_bounded():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {layout:{meta:{mattervis_render:{scene_id:'s', geometry_version:1, display_version:1, camera_version:0}}}, data:[]};
const sandbox = {console, Promise, Number, String, Object, Array, JSON, window:{MatterVisViewCamera:{graph:()=>gd}, Plotly:{react:()=>Promise.resolve()}}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
(async () => {
  for (let i = 0; i < 3000; i += 1) {
    await submit({type:'figure', scene_id:'s', update_kind:'geometry', server_started_at:'2026-01-01T00:00:00', geometry_version:1, display_version:1, camera_version:0, operation_id:'n'+i, figure:{data:[{type:'scatter3d'}], layout:{scene:{}}}});
  }
  assert.ok(sandbox.window.MatterVisViewUpdates.deliveredCount() <= 256);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "view_updates.js",
    )


def test_gesture_generation_cancels_the_previous_settle():
    _node(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const begin = source.indexOf('  function markActive()');
const end = source.indexOf('  // ── Right-click menu');
let timers = [];
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON, Date, window:{}, flushed:0,
  setTimeout:(fn)=>{ const id = timers.push(fn); return id; },
  clearTimeout:(id)=>{ if (id) timers[id-1] = null; },
};
vm.createContext(sandbox);
const prelude = 'let interactionActive=false, settleTimer=null, gestureGeneration=0; const INTERACTION_SETTLE_MS=40; function setInteraction(){} function flushPendingFigurePush(){ flushed += 1; }\n';
vm.runInContext(prelude + source.slice(begin, end) + '\nwindow.testGate={markActive, markInactiveSoon};', sandbox);
sandbox.window.testGate.markActive();
sandbox.window.testGate.markInactiveSoon();
sandbox.window.testGate.markActive();
sandbox.window.testGate.markInactiveSoon();
assert.equal(timers.filter(Boolean).length, 1);
timers.forEach((fn) => { if (fn) fn(); });
assert.equal(sandbox.flushed, 1);
assert.match(source, /INTERACTION_SETTLE_MS = 40/);
""",
        ASSETS / "mattervis.js",
    )
