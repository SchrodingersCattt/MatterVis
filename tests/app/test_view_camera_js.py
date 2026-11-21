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


@pytest.mark.parametrize("outcome", ["afterplot", "mismatch", "generation", "revision"])
def test_projection_settle_cleanup_and_supersession(outcome):
    """Settling releases the FIFO and cannot recover a superseded camera."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    assets = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const gate = fs.readFileSync(process.argv[1], 'utf8');
const source = fs.readFileSync(process.argv[2], 'utf8');
const outcome = process.argv[3];
const camera = type => ({eye:{x:1,y:1,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type}});
let live = camera('perspective'), recoveries = 0, finished = false;
let reachedWait;
const waiting = new Promise(resolve => { reachedWait = resolve; });
const gd = new EventEmitter();
Object.assign(gd, {
  layout: {scene:{camera:live}, meta:{mattervis_render:{scene_id:'a',scene_generation:1}}},
  data: [{type:'scatter3d',x:[1],y:[2],z:[3]}],
  _fullLayout: {scene:{_scene:{getCamera:()=>live}}},
});
gd.once = function (event, listener) {
  const result = EventEmitter.prototype.once.call(this, event, listener);
  if (event === 'plotly_afterplot') reachedWait();
  return result;
};
const sandbox = {Promise,Object,Array,JSON,Number,String,Date,setTimeout,clearTimeout,
  window:{}, document:{getElementById:id=>id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);
// Use the production FIFO so recovery must release its relayout task first.
vm.runInContext('const MAX_RENDER_QUEUE=32;'+gate.slice(gate.indexOf('  function enqueueRender('),
  gate.indexOf('  function figurePushIsCurrent(')), sandbox);
sandbox.window.Plotly = {
  relayout: () => Promise.resolve(),
  react: (_gd, data, layout) => sandbox.window.MatterVisRenderQueue.enqueue(gd, () => {
    recoveries += 1;
    // A permanent mismatch must terminate after one unsuccessful recovery.
    if (outcome !== 'mismatch') live = layout.scene.camera;
  }),
};
vm.runInContext(source, sandbox);
async function check() {
  const apply = sandbox.window.MatterVisViewCamera.applyAsync;
  const first = apply(camera('orthographic'), {scene_id:'a',scene_generation:1,camera_version:1});
  first.then(() => { finished = true; }, () => { finished = true; });
  await waiting;
  assert.equal(gd.listenerCount('plotly_afterplot'), 1, 'camera never reached its settle wait');
  // Work submitted during settle must run before the first camera settles.
  await sandbox.window.MatterVisRenderQueue.enqueue(gd, () => {
    assert.equal(finished, false, 'camera completed before the FIFO progress check');
  });
  if (outcome === 'afterplot') {
    live = camera('orthographic');
    gd.emit('plotly_afterplot');
    assert.equal(await first, true);
  } else if (outcome === 'mismatch') {
    await assert.rejects(first, /camera postcondition failed/);
  } else if (outcome === 'generation') {
    gd.layout.meta.mattervis_render.scene_generation = 2;
    await assert.rejects(first, /stale camera command/);
  } else {
    assert.equal(await apply(camera('perspective'), {
      scene_id:'a',scene_generation:1,camera_version:2,
    }), true);
    await assert.rejects(first, /stale camera command/);
    assert.equal(live.projection.type, 'perspective', 'old recovery replaced the newer camera');
  }
  assert.equal(recoveries, outcome === 'mismatch' ? 1 : 0);
  assert.equal(gd.listenerCount('plotly_afterplot'), 0, 'settle listener leaked');
}
let timer;
Promise.race([
  check(),
  new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('projection settle deadlocked')), 3000); }),
]).catch(error => { console.error(error); process.exitCode = 1; }).finally(() => clearTimeout(timer));
"""
    result = subprocess.run(
        [node, "-e", program, str(assets / "mattervis.js"),
         str(assets / "view_camera.js"), outcome],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_typed_array_coordinates_count_and_a_stale_image_does_not():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const sandbox = {Promise, Object, Array, JSON, Number, String, Date, setTimeout, clearTimeout, window: {}, document: {getElementById: () => null}};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const api = sandbox.window.MatterVisViewCamera;
const ortho = {eye:{x:0,y:0,z:2}, center:{x:0,y:0,z:0}, up:{x:0,y:1,z:0}, projection:{type:'orthographic'}};
function graph(data, images) {
  return {
    data,
    layout: {images: images || [], meta: {mattervis_render: {scene_id: 'scene-b', scene_generation: 9, render_revision: 4}}},
    _fullLayout: {scene: {_scene: {camera: {_ortho: true}}}},
  };
}
const arrays = graph([{type:'scatter3d', x:[0, 1], y:[0, 1], z:[0, 1], marker:{opacity:0}}]);
assert.equal(api.visibleGeometry(arrays), true);
assert.equal(api.postcondition(arrays, ortho, {command:'projection'}), true);
const packed = graph([{type:'scatter3d', marker:{opacity:0}, x:{dtype:'f8', bdata:'AAAAAAAAAAAAAAAAAAAAQA=='}, y:{dtype:'f8', bdata:'AAAAAAAAAAAAAAAAAAAAQA=='}, z:{dtype:'f8', bdata:'AAAAAAAAAAAAAAAAAAAAQA=='}}]);
assert.equal(api.visibleGeometry(packed), true, 'typed-array coordinates were dropped');
assert.equal(api.postcondition(packed, ortho, {command:'projection'}), true);
const emptyPacked = graph([{type:'scatter3d', x:{dtype:'f8', bdata:''}, y:{dtype:'f8', bdata:''}, z:{dtype:'f8', bdata:''}}]);
assert.equal(api.visibleGeometry(emptyPacked), false);
const stale = graph(
  [{type:'scatter3d', x:[], y:[], z:[]}],
  [{source:'data:image/png;base64,STALE', visible:true}],
);
assert.equal(api.visibleGeometry(stale), false);
assert.equal(api.postcondition(stale, ortho, {command:'projection'}), false, 'a stale layout image masked missing coordinates');
const hiddenImage = graph([{type:'scatter3d', x:[], y:[], z:[]}], [{source:'data:image/png;base64,STALE', visible:false}]);
assert.equal(api.postcondition(hiddenImage, ortho, {command:'projection'}), false);
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_fit_aspect_survives_a_scene_plot_that_writes_the_previous_ratio():
    """A scene plot that puts the previous ratio back must leave the fit ratio."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
function glplot() {
  return {
    aspect: {x: 1, y: 1, z: 1},
    setAspectratio(aspect) { this.aspect = {x: aspect.x, y: aspect.y, z: aspect.z}; },
    getAspectratio() { return this.aspect; },
  };
}
const gd = {
  layout: {
    scene: {aspectmode: 'manual', aspectratio: {x: 1, y: 1, z: 1}, camera: {projection: {type: 'orthographic'}}},
    meta: {mattervis_render: {scene_id: 'salt', scene_generation: 1, camera_revision: 1}},
  },
  data: [{type: 'scatter3d', x: [0], y: [0], z: [0]}],
  _fullLayout: {scene: {aspectmode: 'manual', aspectratio: {x: 1, y: 1, z: 1}, _scene: {}}},
};
gd._fullLayout.scene._scene.glplot = glplot();
gd._fullLayout.scene._scene.getCamera = () => ({
  eye: {x: 1.076, y: 1.076, z: 0.965},
  center: {x: 0.037, y: 0.037, z: -0.074},
  up: {x: 0, y: 0, z: 1},
  projection: {type: 'orthographic'},
});
const reacted = [];
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, Math, setTimeout, clearTimeout,
  window: {Plotly: {
    relayout(graph) {
      const scene = graph._fullLayout.scene;
      const fresh = glplot();
      scene._scene.glplot = fresh;
      scene.aspectratio.x = 1;
      scene.aspectratio.y = 1;
      scene.aspectratio.z = 1;
      fresh.setAspectratio({x: 1, y: 1, z: 1});
      return Promise.resolve();
    },
    react(_gd, _data, layout) { reacted.push(layout); return Promise.resolve(); },
  }},
  document: {getElementById: (id) => id === 'crystal-graph' ? {querySelector: () => gd} : null},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const api = sandbox.window.MatterVisViewCamera;
const commanded = {x: 1.0857835859060287, y: 1.0857835859060287, z: 1.0857835859060287};
const fittedRanges = {x: [-0.652, 6.292], y: [-0.652, 6.292], z: [-0.652, 6.292]};
const camera = {
  eye: {x: 1.0762449962100558, y: 1.0762449962100558, z: 0.9652014612038681},
  center: {x: 0.03701451166872919, y: 0.03701451166872919, z: -0.07402902333745838},
  up: {x: 0, y: 0, z: 1},
  projection: {type: 'orthographic'},
};
(async () => {
  assert.equal(await api.applyAsync(camera, {
    scene_id: 'salt', scene_generation: 1, camera_version: 2, command: 'fit',
    aspectratio: commanded, ranges: fittedRanges,
  }), true);
  const layout = gd._fullLayout.scene.aspectratio;
  const painted = gd._fullLayout.scene._scene.glplot.getAspectratio();
  for (const axis of ['x', 'y', 'z']) {
    assert.ok(Math.abs(layout[axis] - commanded[axis]) / commanded[axis] < 0.01, 'layout ' + axis);
    assert.ok(Math.abs(painted[axis] - commanded[axis]) / commanded[axis] < 0.01, 'gl ' + axis);
  }
  gd._fullLayout.scene._scene.glplot.setAspectratio({x: 1, y: 1, z: 1});
  assert.ok(Math.abs(gd._fullLayout.scene.aspectratio.x - commanded.x) / commanded.x < 0.01);
  gd.layout.scene.xaxis = {range: [-2.682, 8.322], autorange: true};
  await api.recover(gd, camera, {command: 'fit', aspectratio: commanded, ranges: fittedRanges, requireEye: true, restoreViewport: true});
  const recovered = reacted.at(-1).scene;
  assert.ok(Math.abs(recovered.aspectratio.x - commanded.x) / commanded.x < 0.01);
  assert.equal(Number(recovered.xaxis.range[0]), fittedRanges.x[0]);
  assert.equal(Number(recovered.xaxis.range[1]), fittedRanges.x[1]);
  assert.equal(recovered.xaxis.autorange, false);
  assert.equal(await api.applyAsync(camera, {
    scene_id: 'salt', scene_generation: 1, camera_version: 3, command: 'reset',
    aspectratio: {x: 1, y: 1, z: 1},
  }), true);
  gd._fullLayout.scene._scene.glplot.setAspectratio({x: 1, y: 1, z: 1});
  assert.equal(gd._fullLayout.scene.aspectratio.x, 1);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr or result.stdout


def test_same_range_orthographic_fit_survives_a_late_scene_plot():
    """Perspective already owns the box. A later plot must not put ratio 1 back."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const source = fs.readFileSync(process.argv[1], 'utf8');
const ranges = {x: [-1.024, 6.664], y: [-1.024, 6.664], z: [-1.024, 6.664]};
const orthoAspect = {x: 1.2208519876003265, y: 1.2208519876003265, z: 1.2208519876003265};
function glplot() {
  return {
    aspect: {x: 1, y: 1, z: 1},
    setAspectratio(aspect) { this.aspect = {x: Number(aspect.x), y: Number(aspect.y), z: Number(aspect.z)}; },
    getAspectratio() { return this.aspect; },
  };
}
const gd = new EventEmitter();
Object.assign(gd, {
  layout: {
    scene: {aspectmode: 'manual', aspectratio: {x: 1, y: 1, z: 1}, camera: {projection: {type: 'perspective'}}},
    meta: {mattervis_render: {scene_id: 'salt', scene_generation: 1, camera_revision: 0}},
  },
  data: [{type: 'scatter3d', x: [0], y: [0], z: [0]}],
  _fullLayout: {scene: {aspectmode: 'manual', aspectratio: {x: 1, y: 1, z: 1}, _scene: {}}},
});
gd._fullLayout.scene._scene.glplot = glplot();
gd._fullLayout.scene._scene.getCamera = () => ({
  eye: {x: 1.076, y: 1.076, z: 0.965},
  center: {x: 0.037, y: 0.037, z: -0.074},
  up: {x: 0, y: 0, z: 1},
  projection: {type: gd.layout.scene.camera.projection.type},
});
const status = {hidden: true, textContent: '', setAttribute() {}};
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, Math, setTimeout, clearTimeout,
  window: {Plotly: {
    relayout(graph, patch) {
      const aspect = graph._fullLayout.scene.aspectratio;
      const scene = graph._fullLayout.scene;
      if (patch['scene.aspectratio.x'] !== undefined) {
        aspect.x = Number(patch['scene.aspectratio.x']);
        aspect.y = Number(patch['scene.aspectratio.y']);
        aspect.z = Number(patch['scene.aspectratio.z']);
        const gl = graph._fullLayout.scene._scene.glplot;
        if (gl && gl.setAspectratio) gl.setAspectratio(aspect);
      }
      ['x', 'y', 'z'].forEach((axis) => {
        const pair = patch['scene.' + axis + 'axis.range'];
        if (!pair) return;
        scene[axis + 'axis'] = scene[axis + 'axis'] || {};
        scene[axis + 'axis'].range = [Number(pair[0]), Number(pair[1])];
        scene[axis + 'axis'].autorange = false;
      });
      if (patch['scene.camera.projection.type']) {
        graph.layout.scene.camera.projection = {type: patch['scene.camera.projection.type']};
      }
      if (patch['scene.camera.eye']) graph.layout.scene.camera.eye = patch['scene.camera.eye'];
      return Promise.resolve();
    },
    react() { return Promise.resolve(); },
  }},
  document: {getElementById: (id) => id === 'mv-render-status' ? status : (id === 'crystal-graph' ? {querySelector: () => gd} : null)},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const api = sandbox.window.MatterVisViewCamera;
const eye = {x: 1.0762449962100558, y: 1.0762449962100558, z: 0.9652014612038681};
const center = {x: 0.03701451166872919, y: 0.03701451166872919, z: -0.07402902333745838};
const cameraFor = (projection) => ({
  eye, center, up: {x: 0, y: 0, z: 1}, projection: {type: projection},
});
function previousRatioPlot() {
  const fresh = glplot();
  gd._fullLayout.scene._scene.glplot = fresh;
  gd._fullLayout.scene.aspectratio.x = 1;
  gd._fullLayout.scene.aspectratio.y = 1;
  gd._fullLayout.scene.aspectratio.z = 1;
  gd.layout.scene.aspectratio.x = 1;
  gd.layout.scene.aspectratio.y = 1;
  gd.layout.scene.aspectratio.z = 1;
  ['x', 'y', 'z'].forEach((axis) => {
    gd._fullLayout.scene[axis + 'axis'] = {range: [-40, 48.164], autorange: true};
  });
  fresh.setAspectratio({x: 1, y: 1, z: 1});
  gd.emit('plotly_afterplot');
}
(async () => {
  assert.equal(await api.applyAsync(cameraFor('perspective'), {
    scene_id: 'salt', scene_generation: 1, camera_version: 1, command: 'fit',
    aspectratio: {x: 1, y: 1, z: 1}, ranges,
  }), true);
  assert.equal(await api.applyAsync(cameraFor('orthographic'), {
    scene_id: 'salt', scene_generation: 1, camera_version: 2, command: 'fit',
    aspectratio: orthoAspect, ranges,
  }), true);
  for (let plot = 0; plot < 5; plot += 1) {
    previousRatioPlot();
    await Promise.resolve();
    await Promise.resolve();
  }
  assert.ok(Math.abs(gd._fullLayout.scene.aspectratio.x - orthoAspect.x) / orthoAspect.x < 0.01);
  assert.ok(Math.abs(gd._fullLayout.scene._scene.glplot.getAspectratio().x - orthoAspect.x) / orthoAspect.x < 0.01);
  ['x', 'y', 'z'].forEach((axis) => {
    const live = gd._fullLayout.scene[axis + 'axis'].range;
    const want = ranges[axis];
    const liveSpan = Math.abs(live[1] - live[0]);
    const wantSpan = Math.abs(want[1] - want[0]);
    assert.ok(Math.abs(liveSpan - wantSpan) <= 1e-3 * Math.max(wantSpan, 1), axis + ' box');
  });
  assert.equal(status.textContent, '');
  await assert.rejects(api.applyAsync(cameraFor('orthographic'), {
    scene_id: 'salt', scene_generation: '', camera_version: 1, command: 'state-confirmed',
    aspectratio: {x: 9, y: 9, z: 9}, ranges,
  }), /stale/);
  assert.ok(Math.abs(gd._fullLayout.scene.aspectratio.x - orthoAspect.x) / orthoAspect.x < 0.01);
  assert.equal(await api.applyAsync(cameraFor('perspective'), {
    scene_id: 'salt', scene_generation: 1, camera_version: 3, command: 'fit',
    aspectratio: {x: 1, y: 1, z: 1}, ranges,
  }), true);
  previousRatioPlot();
  await Promise.resolve();
  await Promise.resolve();
  assert.ok(Math.abs(gd._fullLayout.scene.aspectratio.x - 1) < 0.01, 'perspective fit kept the orthographic ratio');
  const beforeOrbit = gd._fullLayout.scene.aspectratio.x;
  await api.applyAsync({
    eye: {x: 0.4, y: 1.2, z: 0.5}, center, up: {x: 0, y: 0, z: 1},
    projection: {type: 'orthographic'},
  }, {scene_id: 'salt', scene_generation: 1, camera_version: 4});
  gd.emit('plotly_afterplot');
  assert.equal(gd.layout.scene.camera.eye.x, 0.4);
  assert.ok(Math.abs(gd._fullLayout.scene.aspectratio.x - beforeOrbit) / beforeOrbit < 0.01);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr or result.stdout


def test_same_range_fit_keeps_aspect_when_the_replacement_scene_writes_last():
    """The projection change assigns a new gl scene, then that scene writes the old ratio."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const EventEmitter = require('node:events');
const source = fs.readFileSync(process.argv[1], 'utf8');
const ranges = {x: [-1.024, 6.664], y: [-1.024, 6.664], z: [-1.024, 6.664]};
const orthoAspect = {x: 1.2208519876003265, y: 1.2208519876003265, z: 1.2208519876003265};
function glplot() {
  return {
    aspect: {x: 1, y: 1, z: 1},
    setAspectratio(aspect) { this.aspect = {x: Number(aspect.x), y: Number(aspect.y), z: Number(aspect.z)}; },
    getAspectratio() { return this.aspect; },
  };
}
const gd = new EventEmitter();
Object.assign(gd, {
  layout: {
    scene: {aspectmode: 'manual', aspectratio: {x: 1, y: 1, z: 1}, camera: {projection: {type: 'perspective'}}},
    meta: {mattervis_render: {scene_id: 'salt', scene_generation: 1, camera_revision: 0}},
  },
  data: [{type: 'scatter3d', x: [0], y: [0], z: [0]}],
  _fullLayout: {scene: {aspectmode: 'manual', aspectratio: {x: 1, y: 1, z: 1}, _scene: {}}},
});
gd._fullLayout.scene._scene.glplot = glplot();
gd._fullLayout.scene._scene.getCamera = () => ({
  eye: {x: 1.076, y: 1.076, z: 0.965},
  center: {x: 0.037, y: 0.037, z: -0.074},
  up: {x: 0, y: 0, z: 1},
  projection: {type: gd.layout.scene.camera.projection.type},
});
const status = {hidden: true, textContent: '', setAttribute() {}};
let scheduled = null;
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, Math, setTimeout, clearTimeout,
  window: {Plotly: {
    relayout(graph, patch) {
      const scene = graph._fullLayout.scene;
      if (patch['scene.camera.eye']) graph.layout.scene.camera.eye = patch['scene.camera.eye'];
      if (patch['scene.camera.center']) graph.layout.scene.camera.center = patch['scene.camera.center'];
      if (patch['scene.camera.projection.type']) {
        graph.layout.scene.camera.projection = {type: patch['scene.camera.projection.type']};
        graph._fullLayout.scene._scene.glplot = glplot();
      }
      ['x', 'y', 'z'].forEach((axis) => {
        const range = patch['scene.' + axis + 'axis.range'];
        if (range) scene[axis + 'axis'] = {range: range.slice(), autorange: false};
      });
      scheduled = () => {
        const fresh = glplot();
        graph._fullLayout.scene._scene.glplot = fresh;
        const stale = {x: 1, y: 1, z: 1};
        scene.aspectratio.x = stale.x;
        scene.aspectratio.y = stale.y;
        scene.aspectratio.z = stale.z;
        graph.layout.scene.aspectratio.x = stale.x;
        graph.layout.scene.aspectratio.y = stale.y;
        graph.layout.scene.aspectratio.z = stale.z;
        fresh.setAspectratio(stale);
      };
      return Promise.resolve();
    },
    react() { return Promise.resolve(); },
  }},
  document: {getElementById: (id) => id === 'mv-render-status' ? status : (id === 'crystal-graph' ? {querySelector: () => gd} : null)},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const api = sandbox.window.MatterVisViewCamera;
const eye = {x: 1.0762449962100558, y: 1.0762449962100558, z: 0.9652014612038681};
const center = {x: 0.03701451166872919, y: 0.03701451166872919, z: -0.07402902333745838};
const cameraFor = (projection) => ({
  eye, center, up: {x: 0, y: 0, z: 1}, projection: {type: projection},
});
function flushLastWriter() {
  assert.equal(typeof scheduled, 'function');
  scheduled();
}
(async () => {
  assert.equal(await api.applyAsync(cameraFor('perspective'), {
    scene_id: 'salt', scene_generation: 1, camera_version: 1, command: 'fit',
    aspectratio: {x: 1, y: 1, z: 1}, ranges,
  }), true);
  flushLastWriter();
  assert.equal(await api.applyAsync(cameraFor('orthographic'), {
    scene_id: 'salt', scene_generation: 1, camera_version: 2, command: 'fit',
    aspectratio: orthoAspect, ranges,
  }), true);
  flushLastWriter();
  flushLastWriter();
  assert.ok(Math.abs(gd._fullLayout.scene.aspectratio.x - orthoAspect.x) / orthoAspect.x < 0.01);
  assert.ok(Math.abs(gd.layout.scene.aspectratio.x - orthoAspect.x) / orthoAspect.x < 0.01);
  assert.ok(Math.abs(gd._fullLayout.scene._scene.glplot.getAspectratio().x - orthoAspect.x) / orthoAspect.x < 0.01);
  assert.equal(status.textContent, '');
  const beforeOrbit = gd._fullLayout.scene.aspectratio.x;
  assert.equal(await api.applyAsync({
    eye: {x: 0.4, y: 1.2, z: 0.5}, center, up: {x: 0, y: 0, z: 1},
    projection: {type: 'orthographic'},
  }, {scene_id: 'salt', scene_generation: 1, camera_version: 3}), true);
  flushLastWriter();
  assert.equal(gd.layout.scene.camera.eye.x, 0.4);
  assert.ok(Math.abs(gd._fullLayout.scene.aspectratio.x - beforeOrbit) / beforeOrbit < 0.01);
  const figureAspect = {x: 1.069128081202507, y: 1.069128081202507, z: 1.069128081202507};
  const figureLayout = {scene: {
    aspectratio: figureAspect,
    xaxis: {range: ranges.x}, yaxis: {range: ranges.y}, zaxis: {range: ranges.z},
  }};
  assert.equal(api.noteFigureFrame({scene_id: 'salt', camera_revision: 3}, figureLayout), false);
  assert.equal(api.noteFigureFrame({scene_id: 'salt', camera_revision: 4}, figureLayout), true);
  const newer = glplot();
  gd._fullLayout.scene._scene.glplot = newer;
  newer.setAspectratio({x: 1, y: 1, z: 1});
  assert.ok(Math.abs(newer.getAspectratio().x - figureAspect.x) / figureAspect.x < 0.01);
  assert.equal(api.noteFigureFrame({scene_id: 'salt', camera_revision: 4}, figureLayout), false);
  gd.layout.meta.mattervis_render.scene_id = 'other';
  const untouched = glplot();
  gd._fullLayout.scene._scene.glplot = untouched;
  untouched.setAspectratio({x: 0.5, y: 0.25, z: 0.25});
  assert.equal(untouched.getAspectratio().x, 0.5);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
