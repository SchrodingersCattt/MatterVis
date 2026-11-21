"""Empty-renderer guards for preview restore, camera relayout, and context loss."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "frontend" / "assets"


def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    return node


def _run(program, *sources):
    result = subprocess.run(
        [_node(), "-e", program, *[str(path) for path in sources]],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_empty_disorder_preview_does_not_schedule_a_viewport_restore():
    _run(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
let raf = 0;
let setViewport = 0;
const relayouts = [];
const added = [];
const scene = {
  camera: {_ortho: true},
  traces: {atom: {}},
  glplot: {objects: [{}]},
  getCamera() {
    return {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}};
  },
  setViewport() { setViewport += 1; },
};
const gd = {
  on() {},
  data: [],
  __mvGraphGeneration: 3,
  layout: {meta:{mattervis_render:{scene_id:'scene-a'}}},
  _fullLayout: {scene:{_scene: scene}},
};
const sandbox = {
  console, Promise, JSON, Number, String, Object, Array, setTimeout,
  window: {
    requestAnimationFrame() { raf += 1; return 0; },
    MatterVisRenderQueue: {enqueue(_gd, task) { return Promise.resolve().then(task); }},
    Plotly: {
      relayout(_gd, patch) { relayouts.push(patch); return Promise.resolve(); },
      addTraces(_gd, traces) { added.push(traces); return Promise.resolve(); },
      deleteTraces() { return Promise.resolve(); },
    },
    document: {querySelector: () => gd, getElementById: () => gd},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const prepare = sandbox.window.MatterVisDisorderPreview.prepare;
(async () => {
  prepare({replicas: []});
  prepare(null);
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(raf, 0, 'a no-op preview scheduled a frame');
  assert.equal(setViewport, 0, 'a no-op preview called setViewport');
  assert.equal(relayouts.length, 0);
  assert.equal(added.length, 0);

  prepare({replicas: [{id:'r', preview_mesh:{x:[1], y:[1], z:[1], i:[0], j:[0], k:[0]}}]});
  gd.__mvGraphGeneration = 4;
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  assert.equal(added.length, 0, 'a preview for a replaced graph still added traces');
  assert.equal(relayouts.length, 0, 'a replaced graph still restored a camera');
  assert.equal(setViewport, 0);

  sandbox.window.Plotly.addTraces = () => { scene.camera._ortho = false; added.push(['late']); return Promise.resolve(); };
  prepare({replicas: [{id:'r', preview_mesh:{x:[1], y:[1], z:[1], i:[0], j:[0], k:[0]}}]});
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  assert.equal(added.length, 1);
  assert.equal(relayouts.length, 0, 'a projection change still restored the old camera');
  assert.equal(setViewport, 0, 'preview called setViewport');
  assert.equal(relayouts.some((patch) => patch['scene.camera']), false);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "disorder_preview.js",
    )


def test_partial_whole_camera_fields_and_string_form_are_kept():
    _run(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const mattervis = fs.readFileSync(process.argv[1], 'utf8');
const seen = [];
function relayout(_gd, patch) { seen.push(patch); return Promise.resolve(_gd); }
const gd = {layout: {meta:{mattervis_render:{scene_id:'scene-a'}}}, data: []};
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, setTimeout, clearTimeout,
  window: {Plotly:{react: relayout, newPlot: relayout, relayout, update() {}, restyle() {}}},
  document: {getElementById() { return null; }},
};
vm.createContext(sandbox);
const end = mattervis.indexOf('  // ── Right-click menu');
vm.runInContext(mattervis.slice(0, end) + '\n  function refreshSettledGestureStamp() {}\n})();', sandbox);
const plot = sandbox.window.Plotly;
const eye = {x:1.5, y:1.25, z:1.1};
const center = {x:0.2, y:0.3, z:0.4};
const up = {x:0, y:0, z:1};
(async () => {
  await plot.relayout(gd, {"scene.camera": {eye}});
  assert.equal(JSON.stringify(seen.at(-1)['scene.camera.eye']), JSON.stringify(eye));
  assert.equal(seen.at(-1)['scene.camera.center'], undefined);
  assert.equal(seen.at(-1)['scene.camera.projection.type'], undefined);
  assert.equal(seen.at(-1)['scene.camera'], undefined);
  await plot.relayout(gd, {"scene.camera": {center}});
  assert.equal(JSON.stringify(seen.at(-1)['scene.camera.center']), JSON.stringify(center));
  assert.equal(seen.at(-1)['scene.camera.eye'], undefined);
  await plot.relayout(gd, {"scene.camera": {up}});
  assert.equal(JSON.stringify(seen.at(-1)['scene.camera.up']), JSON.stringify(up));
  await plot.relayout(gd, {"scene.camera": {projection: {type: 'perspective'}}});
  assert.equal(seen.at(-1)['scene.camera.projection.type'], 'perspective');
  assert.equal(seen.at(-1)['scene.camera.eye'], undefined);
  await plot.relayout(gd, {"scene.camera": {eye, center, up}});
  assert.equal(seen.at(-1)['scene.camera.projection.type'], undefined, 'a complete pose invented a projection');
  await plot.relayout(gd, {"scene.camera": {eye: {x:9, y:9, z:9}}, "scene.camera.eye": eye});
  assert.equal(JSON.stringify(seen.at(-1)['scene.camera.eye']), JSON.stringify(eye), 'an explicit dotted eye lost to the whole camera');
  await plot.relayout(gd, "scene.camera", {eye, projection: {type: 'orthographic'}});
  assert.equal(seen.at(-1)['scene.camera'], undefined, 'string relayout kept a whole camera');
  assert.equal(JSON.stringify(seen.at(-1)['scene.camera.eye']), JSON.stringify(eye));
  assert.equal(seen.at(-1)['scene.camera.projection.type'], 'orthographic');
  const untouched = {"scene.camera": {tilt: 4}};
  await plot.relayout(gd, untouched);
  assert.equal(JSON.stringify(seen.at(-1)['scene.camera']), JSON.stringify({tilt: 4}), 'an unrecognized camera payload was erased');
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "mattervis.js",
    )


def test_empty_renderer_recovery_stays_retryable_and_queue_drops_a_stale_restore():
    _run(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const mattervis = fs.readFileSync(process.argv[1], 'utf8');
const updates = fs.readFileSync(process.argv[2], 'utf8');
const cameraSource = fs.readFileSync(process.argv[3], 'utf8');
const status = {
  hidden: true, textContent: '', attrs: {},
  setAttribute(name, value) { this.attrs[name] = value; },
  getAttribute(name) { return this.attrs[name] || ''; },
};
const calls = [];
let fill = false;
const gd = {
  on() {},
  data: [{type:'mesh3d', x:[1], y:[1], z:[1], i:[0], j:[0], k:[0]}],
  layout: {scene:{camera:{eye:{x:1,y:0,z:0}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}}}, meta:{mattervis_render:{scene_id:'scene-b', scene_generation:1, render_revision:1, camera_revision:1, server_started_at:'epoch'}}},
  _fullLayout: {scene:{_scene:{
    traces: {},
    camera: {_ortho: false, center: [1, 1, 1]},
    glplot: {objects: [], canvas: {}},
    getCamera() { return {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'perspective'}}; },
  }}},
  __mvSceneCamera: {eye:{x:0,y:1,z:0}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}},
  __mvSceneCameraOwner: 'scene-b',
  __mvGraphGeneration: 2,
  __mvCameraRevision: 4,
};
function react(target, data, lay) {
  const layout = Array.isArray(data) ? lay : data.layout;
  calls.push({kind:'react', datarevision: layout && layout.datarevision, sameData: data === target.data || (data && data.data === target.data)});
  target.layout = layout || target.layout;
  if (fill) {
    target._fullLayout.scene._scene.traces = {atom: {}};
    target._fullLayout.scene._scene.glplot.objects = [{}];
  }
  return Promise.resolve(target);
}
function relayout(_gd, patch) { calls.push({kind:'relayout', patch}); return Promise.resolve(_gd); }
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, setTimeout, clearTimeout,
  requestAnimationFrame() { return 0; },
  cancelAnimationFrame() {},
  window: {Plotly:{react, newPlot: react, relayout, update() {}, restyle() {}}},
  document: {
    getElementById(id) {
      if (id === 'mv-render-status') return status;
      if (id === 'crystal-graph') return {querySelector: () => gd, querySelectorAll: () => []};
      return null;
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  },
};
vm.createContext(sandbox);
sandbox.window.document = sandbox.document;
const end = mattervis.indexOf('  function scheduleOwnedCameraRestore');
vm.runInContext(mattervis.slice(0, end) + '\n})();', sandbox);
vm.runInContext(cameraSource, sandbox);
vm.runInContext(updates, sandbox);
sandbox.window.MatterVisViewCamera.graph = () => gd;
const submit = sandbox.window.MatterVisViewUpdates.submit;
const figure = {
  type: 'figure', scene_id: 'scene-b', update_kind: 'geometry',
  geometry_version: 4, display_version: 5, camera_version: 1, server_started_at: 'epoch',
  figure: {data: gd.data, layout: {scene: {camera: {projection: {type: 'perspective'}}}}},
};
(async () => {
  await assert.rejects(submit(figure), /empty renderer/);
  assert.equal(status.getAttribute('data-level'), 'error');
  assert.match(status.textContent, /did not finish drawing/);
  assert.equal(calls.filter((item) => item.kind === 'react').length, 2, 'recovery did not replay the geometry');
  assert.match(String(calls[1].datarevision), /#recover/, 'recovery reused the same datarevision');
  const retried = submit(figure);
  assert.notEqual(retried, 'stale', 'a failed frame was deduplicated');
  fill = true;
  assert.equal(await retried, 'figure');
  assert.equal(status.hidden, true, 'a successful retry left the error banner up');
  assert.equal(status.textContent, '');
  assert.equal(submit(figure), 'stale');

  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const queued = sandbox.window.MatterVisRenderQueue.enqueue(gd, () => {
    gd.__mvRenderQueueState.reentrantToken = {owner: true};
    const inline = sandbox.window.Plotly.relayout(gd, {'scene.camera.eye': {x: 3, y: 3, z: 3}});
    gd.__mvRenderQueueState.reentrantToken = null;
    return Promise.resolve(inline).then(() => gate);
  });
  await Promise.resolve();
  const external = sandbox.window.Plotly.relayout(gd, {'scene.camera.eye': {x: 8, y: 8, z: 8}});
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.some((item) => item.kind === 'relayout' && item.patch['scene.camera.eye'] && item.patch['scene.camera.eye'].x === 3), true, 'internal relayout waited behind itself');
  assert.equal(calls.some((item) => item.kind === 'relayout' && item.patch['scene.camera.eye'] && item.patch['scene.camera.eye'].x === 8), false, 'external relayout cut ahead of the frame');
  sandbox.window.__mvGestureSceneId = 'scene-a';
  gd.layout.meta.mattervis_render.scene_id = 'scene-b';
  gd.__mvSceneCamera = {eye:{x:0,y:1,z:0}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}};
  gd.__mvSceneCameraOwner = 'scene-b';
  gd._fullLayout.scene._scene.camera.center = [4, 4, 4];
  sandbox.window.__mvTestRestoreOwnedCamera(0, 'scene-a');
  assert.equal(gd.__mvForeignRestore, true, 'owned-camera restore was not queued');
  gd.__mvGraphGeneration = 9;
  release();
  await queued;
  await external;
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.some((item) => item.kind === 'relayout' && item.patch['scene.camera.eye'] && item.patch['scene.camera.eye'].x === 0), false, 'a stale owned-camera restore was applied');
  assert.equal(gd.__mvForeignRestore, false);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "mattervis.js",
        ASSETS / "view_updates.js",
        ASSETS / "view_camera.js",
    )


def test_visible_geometry_accepts_binary_coordinates_and_hidden_figures():
    _run(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {console, Promise, JSON, Number, Date, Object, Array, String, Math, setTimeout, clearTimeout, window: {Plotly:{}}, document:{getElementById(){return null;}}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const view = sandbox.window.MatterVisViewCamera;
const empty = {data: []};
assert.equal(view.visibleGeometry(empty), false);
assert.equal(view.postcondition(empty, {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}}), true);
const hidden = {data: [{type:'scatter3d', x:[1], y:[1], z:[1], visible:false}, {type:'mesh3d', x:[1], i:[0], visible:'legendonly'}]};
assert.equal(view.visibleGeometry(hidden), false);
assert.equal(view.postcondition(hidden, {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}}), true);
const flat = {data: [{type:'scatter', x:[1], y:[1]}]};
assert.equal(view.visibleGeometry(flat), false);
const binary = {data: [{type:'scatter3d', x:{dtype:'f8', bdata:'aaaa'}, y:{dtype:'f8', bdata:'bbbb'}, z:{dtype:'f8', bdata:'cccc'}}]};
assert.equal(view.visibleGeometry(binary), true);
const wiped = {data: [{type:'scatter3d', x:{dtype:'f8', bdata:'aaaa'}, y:{dtype:'f8', bdata:'bbbb'}, z:{dtype:'f8', bdata:'cccc'}}], _fullLayout:{scene:{_scene:{traces:{}, glplot:{objects:[]}}}}};
assert.equal(view.visibleGeometry(wiped), false);
assert.equal(view.postcondition(wiped, {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'perspective'}}), false);
""",
        ASSETS / "view_camera.js",
    )


def test_whole_camera_relayout_is_dotted_and_empty_renderer_fails_postcondition():
    _run(
        r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const mattervis = fs.readFileSync(process.argv[1], 'utf8');
const cameraSource = fs.readFileSync(process.argv[2], 'utf8');
const seen = [];
let canvas = {id: 'first', addEventListener() {}};
const nextCanvas = {id: 'second', addEventListener() {}};
const gd = {
  layout: {meta:{mattervis_render:{scene_id:'scene-a', render_revision:1, server_started_at:'epoch'}}},
  data: [{type:'mesh3d', x:[1], y:[1], z:[1], i:[0], j:[0], k:[0]}],
  _fullLayout: {scene:{_scene:{
    traces: {},
    camera: {_ortho: true},
    glplot: {objects: [], canvas, contextLost: false},
    getCamera() { return {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}}; },
  }}},
};
function relayout(_gd, patch) {
  seen.push(patch);
  gd._fullLayout.scene._scene.glplot.canvas = nextCanvas;
  return Promise.resolve(_gd);
}
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, setTimeout, clearTimeout,
  window: {Plotly:{react: relayout, newPlot: relayout, relayout, update() {}, restyle() {}}},
  document: {getElementById() { return null; }},
};
vm.createContext(sandbox);
const end = mattervis.indexOf('  // ── Right-click menu');
vm.runInContext(mattervis.slice(0, end) + '\n  function refreshSettledGestureStamp() {}\n})();', sandbox);
vm.runInContext(cameraSource, sandbox);
(async () => {
  const plot = sandbox.window.Plotly;
  const view = sandbox.window.MatterVisViewCamera;
  view.installContextLoss(gd);
  assert.equal(gd.__mvContextLossCanvas, canvas);
  await plot.relayout(gd, {"scene.camera": {
    eye:{x:1.2,y:1.3,z:1.4}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'perspective'},
  }});
  assert.equal(seen.length, 1);
  assert.equal(seen[0]['scene.camera'], undefined, 'whole scene.camera reached Plotly');
  assert.equal(seen[0]['scene.camera.projection.type'], 'perspective');
  assert.equal(seen[0]['scene.camera.eye'].x, 1.2);
  assert.equal(gd.__mvContextLossCanvas, nextCanvas, 'context-loss listener stayed on the retired canvas');

  assert.equal(view.visibleGeometry(gd), false, 'empty scene.traces and glplot.objects still counted as visible');
  assert.equal(view.postcondition(gd, {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}}), false);
  gd._fullLayout.scene._scene.traces = {atom: {}};
  gd._fullLayout.scene._scene.glplot.objects = [{bounds: [[0, 0, 0], [1, 1, 1]]}];
  gd._fullLayout.scene._scene.camera._ortho = true;
  assert.equal(view.visibleGeometry(gd), true);
  assert.equal(view.postcondition(gd, {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}}), true);
  const replaced = sandbox.window.__mvRendererDiag.filter((item) => item.kind === 'canvas-replaced');
  assert.ok(replaced.length >= 1 && replaced.length <= 20);
})().catch((error) => { console.error(error); process.exitCode = 1; });
""",
        ASSETS / "mattervis.js",
        ASSETS / "view_camera.js",
    )
