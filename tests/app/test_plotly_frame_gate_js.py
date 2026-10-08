"""Run the real frame gate, both Plotly signatures and live camera reader."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_serialized_frame_gate_and_live_camera():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
let expected={scene_id:'a',render_revision:1,server_started_at:'epoch'};
const camera=x=>({eye:{x,y:1,z:2},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1}});
let live=camera(9), calls=[], finish=null, fail=false, hold=false;
function layout(rev=1, ui='a__0', scene='a', epoch='epoch') {
  return {scene:{camera:camera(1),uirevision:ui},meta:{mattervis_render:{scene_id:scene,render_revision:rev,server_started_at:epoch}}};
}
const gd={layout:layout(0),_fullLayout:{scene:{camera:camera(-8),_scene:{getCamera:()=>live}}}};
function original(target, data, lay) {
  const l=Array.isArray(data)?lay:data.layout;
  calls.push(l);
  if(fail) {fail=false; return Promise.reject(new Error('render failed'));}
  if(hold) return new Promise(resolve=>{finish=()=>{target.layout=l;resolve(target);};});
  target.layout=l;
  return Promise.resolve(target);
}
const sandbox={console,Promise,JSON,Number,Date,Object,Array,String,setTimeout:()=>{},clearTimeout:()=>{},
  window:{Plotly:{react:original,newPlot:original}},
  document:{getElementById:id=>id==='fast-view-metadata'?{textContent:JSON.stringify(expected)}:
    id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);
const end=source.indexOf('  // ── Right-click menu');
vm.runInContext(source.slice(0,end)+
  'window.testGate={markActive,stop:()=>{interactionActive=false;flushPendingFigurePush();}};})();',sandbox);
const start=source.indexOf('  function liveSceneCamera(');
const stop=source.indexOf('  function redrawCompass(',start);
vm.runInContext(source.slice(start,stop),sandbox);
const plot=sandbox.window.Plotly;
(async()=>{
  // Installed Dash uses react(gd,{data,layout}); WS uses positional args.
  await plot.react(gd,{data:[],layout:layout(0)});
  assert.equal(calls.length,0,'stale object signature escaped gate');
  await plot.react(gd,[],layout(0));
  assert.equal(calls.length,0,'stale positional signature escaped gate');
  await plot.newPlot(gd,{data:[],layout:layout(1,'a__0','b')});
  await plot.react(gd,[],layout(1,'a__0','a','old-epoch'));
  assert.equal(calls.length,0,'wrong scene/epoch escaped gate');
  await plot.react(gd,{data:[],layout:layout()});
  assert.equal(calls[0].scene.camera.eye.x,9,'did not read live GL camera');
  await plot.react(gd,[],layout());
  assert.equal(calls.length,1,'WS/Dash delivered same successful frame twice');

  // Reset/align must honor the incoming camera despite stale live GL state.
  await plot.react(gd,[],layout(1,'a__1'));
  assert.equal(calls[1].scene.camera.eye.x,1,'reset camera overwritten');
  await plot.newPlot(gd,{data:[],layout:layout(1,'a__2')});
  assert.equal(calls[2].scene.camera.eye.x,1,'align camera overwritten');
  expected={...expected,scene_id:'b'};
  await plot.react(gd,[],layout(1,'b__0','b'));
  assert.equal(calls[3].scene.camera.eye.x,1,'previous scene camera leaked');

  // Serialization, successful-only dedup and live acquisition at execution.
  expected={...expected,render_revision:2};
  hold=true;
  const first=plot.react(gd,{data:[],layout:layout(2,'b__0','b')});
  const duplicate=plot.react(gd,[],layout(2,'b__0','b'));
  await Promise.resolve();await Promise.resolve();
  assert.equal(calls.length,5);
  finish();await first;await duplicate;
  assert.equal(calls.length,5,'queued duplicate was not suppressed');
  hold=false; fail=true;
  expected={...expected,render_revision:3};
  await assert.rejects(plot.react(gd,[],layout(3,'b__0','b')),/render failed/);
  live=camera(17);
  await plot.react(gd,{data:[],layout:layout(3,'b__0','b')});
  assert.equal(calls.length,7,'failure incorrectly marked delivered');
  assert.equal(calls[6].scene.camera.eye.x,17);

  // A queued frame superseded before it can run must never reach Plotly.
  hold=true; expected={...expected,render_revision:4};
  const running=plot.react(gd,[],layout(4,'b__0','b'));
  await Promise.resolve();await Promise.resolve();
  const stale=plot.react(gd,{data:[],layout:layout(4,'b__1','b')});
  expected={...expected,render_revision:5};
  finish();await running;await stale;
  assert.equal(calls.length,8,'stale queued frame delivered');
  hold=false;
  sandbox.window.testGate.markActive();
  const duringDrag=plot.react(gd,{data:[],layout:layout(5,'b__0','b')});
  await Promise.resolve();await Promise.resolve();
  assert.equal(calls.length,8,'Dash interrupted live interaction');
  live=camera(23);
  sandbox.window.testGate.stop();await duringDrag;
  assert.equal(calls[8].scene.camera.eye.x,23,'camera sampled before interaction settled');

  expected={...expected,camera_revision:1};
  await plot.react(gd,[],layout(5,'b__old','b'));
  assert.equal(calls.length,9,'obsolete camera revision escaped gate');
  const reset=layout(5,'b__1','b');
  reset.meta.mattervis_render.camera_revision=1;
  await plot.react(gd,{data:[],layout:reset});
  assert.equal(calls[9].scene.camera.eye.x,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr