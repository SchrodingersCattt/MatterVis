"""Regression coverage for serialized client-side camera relayouts."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_camera_projection_and_reset_are_applied_in_order():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {};
const calls = [];
const pending = [];
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, setTimeout, clearTimeout,
  window: {
    Plotly: {
      relayout: (_gd, patch) => {
        calls.push(patch);
        return new Promise((resolve, reject) => pending.push({resolve, reject}));
      },
    },
  },
  document: {getElementById: id => id === 'crystal-graph' ? {querySelector: () => gd} : null},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const apply = sandbox.window.MatterVisViewCamera.apply;
const camera = type => ({
  eye: {x: 0, y: 0, z: 1.8},
  center: {x: 0, y: 0, z: 0},
  up: {x: 0, y: 1, z: 0},
  projection: {type},
});
(async () => {
  const waitForCall = async expected => {
    for (let i = 0; i < 8 && calls.length < expected; i++) await Promise.resolve();
    assert.equal(calls.length, expected);
  };
  assert.equal(apply(camera('orthographic')), true);
  assert.equal(apply(camera('perspective')), true);
  assert.equal(apply(camera('orthographic')), true); // reset after the switch
  await waitForCall(1);
  pending.shift().resolve();
  await waitForCall(2);
  pending.shift().reject(new Error('projection relayout failed'));
  await waitForCall(3);
  assert.equal(calls[2]['scene.camera.projection.type'], 'orthographic', 'reset camera was reordered');
  pending.shift().resolve();
  await gd.__mvRenderQueue;
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_camera_uses_projection_safe_patch_and_validates_live_scene():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
let camera = {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'perspective'}};
const gd = {layout:{scene:{camera}, meta:{mattervis_render:{scene_id:'a', scene_generation:7}}}, data:[{type:'scatter3d', x:[0], y:[0], z:[0]}], _fullLayout:{scene:{_scene:{getCamera:()=>camera}}}};
const calls = [];
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, setTimeout, clearTimeout,
  window: {
    Plotly: {
      relayout: (_gd, patch) => {
        calls.push(patch);
        if (patch['scene.camera.projection.type']) camera.projection = {type: patch['scene.camera.projection.type']};
        return Promise.resolve();
      },
      react: (_gd, _data, layout) => { camera = layout.scene.camera; gd.layout = layout; return Promise.resolve(); },
    },
  },
  document: {getElementById: id => id === 'crystal-graph' ? {querySelector: () => gd} : null},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
  const apply = sandbox.window.MatterVisViewCamera.applyAsync;
  const make = type => ({eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type}});
  await apply(make('orthographic'), {scene_id:'a', scene_generation:7, graph_generation:0});
  assert.equal(calls[0]['scene.camera.projection.type'], 'orthographic');
  assert.equal(calls[0]['scene.camera'], undefined, 'whole-camera relayout escaped');
  assert.equal(sandbox.window.MatterVisViewCamera.actualCamera(gd).projection.type, 'orthographic');
  assert.equal(sandbox.window.MatterVisViewCamera.visibleGeometry(gd), true);
  gd.layout.meta.mattervis_render.scene_generation = 8;
  await assert.rejects(apply(make('perspective'), {scene_id:'a', scene_generation:7, graph_generation:0}), /stale/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_camera_timeout_replaces_wedged_queue():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {layout:{meta:{mattervis_render:{scene_id:'a', scene_generation:1}}}, data:[]};
const calls = [];
const pending = [];
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, setTimeout, clearTimeout,
  window: {MATTERVIS_CAMERA_TIMEOUT_MS:20, Plotly:{relayout:(_gd, patch)=>{calls.push(patch); return new Promise((resolve)=>pending.push(resolve));}}},
  document:{getElementById:id=>id === 'crystal-graph' ? {querySelector:()=>gd} : null},
};
vm.createContext(sandbox); vm.runInContext(source, sandbox);
(async()=>{
  const camera = type => ({eye:{x:1,y:1,z:1},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type}});
  const options = {scene_id:'a', scene_generation:1, graph_generation:0};
  let hungError = null;
  const hung = sandbox.window.MatterVisViewCamera.applyAsync(camera('perspective'), options);
  hung.catch(error => { hungError = error; });
  const reset = sandbox.window.MatterVisViewCamera.applyAsync(camera('orthographic'), options);
  for (let i = 0; i < 50 && calls.length < 2; i++) await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(calls.length, 2, 'queued Reset did not start after the hung relayout timed out');
  assert.equal(calls[1]['scene.camera.projection.type'], 'orthographic');
  assert.equal(gd.__mvCameraQueueRecovery.reason, 'camera relayout timeout');
  pending[1]();
  await reset;
  assert.match(String(hungError && hungError.message), /timeout/);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_camera_snapshot_survives_mutation_and_rejected_relayout_recovers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
let camera = {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'perspective'}};
const gd = {layout:{scene:{camera}, meta:{mattervis_render:{scene_id:'a', scene_generation:1}}}, data:[{type:'scatter3d', x:[0], y:[0], z:[0]}], _fullLayout:{scene:{_scene:{getCamera:()=>camera}}}};
const calls = [];
let reactCount = 0;
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, setTimeout, clearTimeout,
  window: {Plotly:{
    relayout: () => { calls.push('relayout'); return Promise.reject(new Error('projection relayout failed')); },
    react: (_gd, _data, layout) => { reactCount += 1; camera = layout.scene.camera; gd.layout = layout; return Promise.resolve(); },
  }},
  document:{getElementById:id=>id === 'crystal-graph' ? {querySelector:()=>gd} : null},
};
vm.createContext(sandbox); vm.runInContext(source, sandbox);
(async()=>{
  const requested = {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}};
  const applied = sandbox.window.MatterVisViewCamera.applyAsync(requested, {scene_id:'a', scene_generation:1, graph_generation:0});
  requested.projection.type = 'perspective';
  await applied;
  assert.equal(calls.length, 1);
  assert.equal(reactCount, 1, 'rejected relayout did not recover through react');
  assert.equal(sandbox.window.MatterVisViewCamera.actualCamera(gd).projection.type, 'orthographic');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("reject_relayout", [False, True])
def test_camera_recovery_does_not_wait_behind_its_own_render_task(reject_relayout):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    assets = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const gate = fs.readFileSync(process.argv[1], 'utf8');
const source = fs.readFileSync(process.argv[2], 'utf8');
const camera = type => ({eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type}});
let live = camera('perspective');
const gd = {layout:{scene:{camera:live}, meta:{mattervis_render:{scene_id:'a',scene_generation:1}}},
  data:[{type:'scatter3d',x:[0],y:[0],z:[0]}], _fullLayout:{scene:{_scene:{getCamera:()=>live}}}};
const sandbox = {Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,
  window:{}, document:{getElementById:id=>id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);
// Use the production coordinator, including its non-reentrant FIFO.
vm.runInContext('const MAX_RENDER_QUEUE=32;'+gate.slice(gate.indexOf('  function enqueueRender('),
  gate.indexOf('  function figurePushIsCurrent(')), sandbox);
sandbox.window.Plotly = {
  relayout: () => process.argv[3] === 'True' ? Promise.reject(new Error('relayout failed')) : Promise.resolve(),
  react: (_gd, data, layout) => sandbox.window.MatterVisRenderQueue.enqueue(gd, () => {
    live = layout.scene.camera; gd.layout = layout;
  }),
};
vm.runInContext(source, sandbox);
(async () => {
  let timer;
  try {
    await Promise.race([
      sandbox.window.MatterVisViewCamera.applyAsync(camera('orthographic'), {scene_id:'a',scene_generation:1}),
      new Promise((_, reject) => { timer=setTimeout(()=>reject(new Error('camera recovery deadlocked')), 500); }),
    ]);
    assert.equal(live.projection.type, 'orthographic');
  } finally { clearTimeout(timer); }
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", program, str(assets / "mattervis.js"),
         str(assets / "view_camera.js"), str(reject_relayout)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("replay_outcome", ["noop", "reject", "superseded"])
def test_camera_timeout_replay_releases_queue_before_recovery(replay_outcome):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    assets = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const gate = fs.readFileSync(process.argv[1], 'utf8');
const source = fs.readFileSync(process.argv[2], 'utf8');
const outcome = process.argv[3];
const camera = type => ({eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type}});
let live = camera('perspective'), relayouts = 0, recoveries = 0, releaseReplay;
let replayStarted, recoveryFinished;
const replay = new Promise(resolve => { replayStarted = resolve; });
const recovered = new Promise(resolve => { recoveryFinished = resolve; });
const gd = {layout:{scene:{camera:live}, meta:{mattervis_render:{scene_id:'a',scene_generation:1}}},
  data:[{type:'scatter3d',x:[0],y:[0],z:[0]}], _fullLayout:{scene:{_scene:{getCamera:()=>live}}}};
const sandbox = {Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,
  window:{MATTERVIS_CAMERA_TIMEOUT_MS:20},
  document:{getElementById:id=>id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);
vm.runInContext('const MAX_RENDER_QUEUE=32;'+gate.slice(gate.indexOf('  function enqueueRender('),
  gate.indexOf('  function figurePushIsCurrent(')), sandbox);
sandbox.window.Plotly = {
  relayout: () => {
    relayouts += 1;
    if (relayouts === 1) return new Promise(() => {});
    if (relayouts === 2) return new Promise((resolve, reject) => {
      releaseReplay = () => outcome === 'reject' ? reject(new Error('replay failed')) : resolve();
      replayStarted();
    });
    return Promise.resolve();
  },
  react: (_gd, data, layout) => {
    recoveries += 1;
    return sandbox.window.MatterVisRenderQueue.enqueue(gd, () => {
      live = layout.scene.camera; gd.layout = layout; recoveryFinished();
    });
  },
};
vm.runInContext(source, sandbox);
(async () => {
  let timer;
  const work = (async () => {
    const apply = sandbox.window.MatterVisViewCamera.applyAsync;
    const options = {scene_id:'a',scene_generation:1};
    await assert.rejects(apply(camera('orthographic'), options), /timeout/);
    await replay;
    // No version change: the latest-camera identity must also fence replay.
    const newer = outcome === 'superseded' ? apply(camera('perspective'), options) : null;
    releaseReplay();
    if (newer) await newer;
    else await recovered;
    let laterTaskRan = false;
    await sandbox.window.MatterVisRenderQueue.enqueue(gd, () => { laterTaskRan = true; });
    assert.equal(laterTaskRan, true, 'replay recovery blocked later frame work');
    assert.equal(recoveries, newer ? 0 : 1, 'a superseded replay restored its old camera');
    assert.equal(live.projection.type, newer ? 'perspective' : 'orthographic');
  })();
  try {
    await Promise.race([work, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('camera timeout replay deadlocked')), 500);
    })]);
  } finally { clearTimeout(timer); }
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", program, str(assets / "mattervis.js"),
         str(assets / "view_camera.js"), replay_outcome],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_newer_camera_revision_bypasses_never_settling_older_command():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const gd={layout:{scene:{camera:{projection:{type:'perspective'}}},meta:{mattervis_render:{scene_id:'a',scene_generation:1}}},data:[]};
let calls=0;
const sandbox={Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,window:{MATTERVIS_CAMERA_TIMEOUT_MS:20,Plotly:{relayout:(_gd,patch)=>{calls+=1; if(calls===1)return new Promise(()=>{}); _gd.layout.scene.camera.projection={type:patch['scene.camera.projection.type']}; return Promise.resolve();}}},document:{getElementById:id=>id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
(async()=>{const apply=sandbox.window.MatterVisViewCamera.applyAsync;const cam=t=>({eye:{x:1,y:1,z:1},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type:t}});const first=apply(cam('perspective'),{scene_id:'a',scene_generation:1,camera_version:1,graph_generation:0});await Promise.resolve();await Promise.resolve();const newer=apply(cam('orthographic'),{scene_id:'a',scene_generation:1,camera_version:2,graph_generation:0});assert.equal(await newer,true);assert.equal(calls,2);await assert.rejects(first,/stale|timeout/);})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_standalone_camera_api_drops_an_older_revision_before_relayout():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const gd={layout:{scene:{camera:{projection:{type:'perspective'}}},meta:{mattervis_render:{scene_id:'a',scene_generation:1}}},data:[]};
const calls=[]; let release;
const sandbox={Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,window:{
  Plotly:{relayout:(_gd,patch)=>{calls.push(patch); _gd.layout.scene.camera.projection={type:patch['scene.camera.projection.type']}; return new Promise(resolve=>{release=resolve;});}},
},document:{getElementById:id=>id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
(async()=>{
  const camera=type=>({eye:{x:1,y:1,z:1},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type}});
  const apply=sandbox.window.MatterVisViewCamera.applyAsync;
  const newest=apply(camera('orthographic'),{scene_id:'a',scene_generation:1,camera_version:3,graph_generation:0});
  await Promise.resolve(); await Promise.resolve();
  await assert.rejects(apply(camera('perspective'),{scene_id:'a',scene_generation:1,camera_version:2,graph_generation:0}),/stale/);
  assert.equal(calls.length,1,'older standalone camera reached Plotly');
  release(); await newest;
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_projection_canvas_replacement_does_not_report_context_loss():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const listeners={};
const retired={isConnected:false,addEventListener:(name,fn)=>{listeners[name]=fn;}};
const live={isConnected:true};
const gd={data:[],_fullLayout:{scene:{_scene:{glplot:{canvas:retired}}}}};
const sandbox={Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,window:{Plotly:{},document:{getElementById:()=>({querySelector:()=>gd})}}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
const api=sandbox.window.MatterVisViewCamera;
api.installContextLoss(gd);
retired.__mvContextRetired=true;
listeners.webglcontextlost({preventDefault(){}});
assert.equal(sandbox.window.__mvRenderStatus,undefined,'retired Plotly canvas raised a false loss');
gd._fullLayout.scene._scene.glplot.canvas=live;
listeners.webglcontextlost({preventDefault(){}});
assert.equal(sandbox.window.__mvRenderStatus,undefined,'replaced Plotly canvas raised a false loss');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_context_loss_listener_rebinds_when_plotly_replaces_canvas():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
function canvas(){return {isConnected:true,listeners:{},addEventListener(name,fn){this.listeners[name]=fn;}};}
const first=canvas(), second=canvas();
const scene={glplot:{canvas:first}};
const gd={data:[],_fullLayout:{scene:{_scene:scene}}};
const sandbox={Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,
  window:{Plotly:{},document:{getElementById:()=>({querySelector:()=>gd})}}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
const api=sandbox.window.MatterVisViewCamera;
api.installContextLoss(gd);
assert.equal(typeof first.listeners.webglcontextlost,'function');
scene.glplot.canvas=second;
api.installContextLoss(gd);
assert.equal(typeof second.listeners.webglcontextlost,'function','replacement canvas did not receive a listener');
assert.equal(gd.__mvContextLossCanvas,second);
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_camera_delivery_retries_while_plotly_replaces_the_graph():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
let ready=false;
const gd={layout:{scene:{camera:{projection:{type:'perspective'}}}},data:[]};
const status={hidden:true,textContent:'',setAttribute(){}};
const sandbox={Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,window:{setTimeout,clearTimeout,Plotly:{relayout:(_gd,patch)=>{_gd.layout.scene.camera.projection={type:patch['scene.camera.projection.type']};return Promise.resolve();}},document:{getElementById:(id)=>id==='mv-render-status'?status:{querySelector:()=>ready?gd:null}}}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
(async()=>{
  const camera={eye:{x:1,y:1,z:1},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type:'orthographic'}};
  const pending=sandbox.window.MatterVisViewCamera.applyAsync(camera,{scene_id:'a',scene_generation:1,graph_generation:0});
  setTimeout(()=>{ready=true;},40);
  assert.equal(await pending,true);
  assert.equal(gd.layout.scene.camera.projection.type,'orthographic');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_perspective_wheel_zoom_stays_outside_the_model_box():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const listeners = [];
const canvas = {};
const gd = {
  isConnected: true,
  _fullLayout: {
    scene: {
      aspectratio: {x: 1, y: 1, z: 1},
      _scene: {glplot: {canvas}, camera: {eye: [0.2, 0.2, 0.2], center: [0, 0, 0], up: [0, 0, 1], _ortho: false}},
    },
  },
};
const calls = [];
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, Math, setTimeout, clearTimeout,
  window: {Plotly: {relayout: (_gd, patch) => { calls.push(patch); return Promise.resolve(); }}},
  document: {addEventListener: (type, fn) => listeners.push({type, fn})},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
sandbox.window.MatterVisViewCamera.installWheelZoom(gd);
const wheel = listeners.find(item => item.type === 'wheel').fn;
wheel({
  target: canvas,
  deltaY: -800,
  deltaX: 0,
  preventDefault() {},
  stopImmediatePropagation() {},
});
assert.equal(calls.length, 1);
const eye = calls[0]['scene.camera.eye'];
const dist = Math.hypot(eye.x, eye.y, eye.z);
const limit = sandbox.window.MatterVisViewCamera.minimumPerspectiveEyeDistance(
  eye, {x: 0, y: 0, z: 0}, {x: 1, y: 1, z: 1},
);
assert.ok(dist + 1e-6 >= limit, 'eye ' + dist + ' entered the near plane, limit ' + limit);
assert.ok(dist > 0.5, 'max zoom still uses the old 0.35 eye floor inside the cube');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_orthographic_postcondition_waits_for_a_late_gl_flip():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
let ortho = false, reactCalls = 0;
const gd = {
  layout: {scene: {camera: {projection: {type: 'perspective'}}}},
  data: [],
  _fullLayout: {scene: {_scene: {getCamera() {
    const type = ortho ? 'orthographic' : 'perspective';
    return {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type}};
  }}}},
};
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, setTimeout, clearTimeout,
  window: {Plotly: {
    relayout: () => {
      setTimeout(() => { ortho = true; }, 40);
      return Promise.resolve();
    },
    react: () => { reactCalls += 1; return Promise.resolve(); },
  }},
  document: {getElementById: id => id === 'crystal-graph' ? {querySelector: () => gd} : null},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
  const camera = {eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}};
  assert.equal(await sandbox.window.MatterVisViewCamera.applyAsync(camera, {scene_id:'a', scene_generation:1}), true);
  assert.equal(ortho, true, 'settled before the GL projection flipped');
  assert.equal(reactCalls, 0, 'a late flip was treated as a failed camera update');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
