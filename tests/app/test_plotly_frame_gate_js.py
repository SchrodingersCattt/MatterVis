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
const start=source.indexOf('  function displayedSceneCamera(');
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


def test_replacement_frame_uses_envelope_revision_over_stale_layout_metadata():
    """A selected-scene frame must replace the old canvas even if Dash's layout stamp lags."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const expected={scene_id:'scene-b',render_revision:1,server_started_at:'epoch'};
const layout=(scene,revision)=>({scene:{},meta:{mattervis_render:{scene_id:scene,render_revision:revision,server_started_at:'epoch'}}});
const gd={layout:layout('scene-a',0),_fullLayout:{scene:{camera:{},_scene:{getCamera:()=>({})}}}};
const calls=[];
function react(target,data,lay) { calls.push(lay); target.layout=lay; return Promise.resolve(target); }
const sandbox={console,Promise,JSON,Number,Date,Object,Array,String,setTimeout,clearTimeout,
  window:{Plotly:{react,newPlot:react}},
  document:{getElementById:id=>id==='fast-view-metadata'?{textContent:JSON.stringify(expected)}:
    id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);
const end=source.indexOf('  // ── Right-click menu');
vm.runInContext(source.slice(0,end)+'})();',sandbox);
(async()=>{
  // The envelope says scene-b/revision 1; its embedded figure layout is one
  // Dash turn behind (scene-b/revision 0). The replacement must still paint.
  await sandbox.window.Plotly.react(gd,[],layout('scene-b',0));
  assert.equal(calls.length,1,'replacement frame was rejected as stale');
  assert.equal(calls[0].meta.mattervis_render.scene_id,'scene-b');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_parked_frames_are_bounded_and_newest_frame_wins():
    """A lost pointer-up must not replay an obsolete parked frame forever."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
let expected={scene_id:'a',render_revision:1,server_started_at:'epoch'};
let calls=[];
function layout(rev) {
  return {scene:{camera:{eye:{x:rev,y:1,z:2},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1}},uirevision:'a__'+rev},
    meta:{mattervis_render:{scene_id:'a',render_revision:rev,server_started_at:'epoch'}}};
}
const gd={layout:layout(0),_fullLayout:{scene:{camera:layout(0).scene.camera}}};
function original(target,data,lay) { calls.push(lay); target.layout=lay; return Promise.resolve(target); }
const sandbox={console,Promise,JSON,Number,Date,Object,Array,String,setTimeout,clearTimeout,
  window:{Plotly:{react:original,newPlot:original}},
  document:{getElementById:id=>id==='fast-view-metadata'?{get textContent(){return JSON.stringify(expected);}}:
    id==='crystal-graph'?{querySelector:()=>gd}:null}};
vm.createContext(sandbox);
const end=source.indexOf('  // ── Right-click menu');
vm.runInContext(source.slice(0,end)+
  'window.testGate={start:markActive,stop:()=>{interactionActive=false;flushPendingFigurePush();}};})();',sandbox);
const plot=sandbox.window.Plotly;
(async()=>{
  sandbox.window.testGate.start();
  const old=plot.react(gd,[],layout(1));
  expected={...expected,render_revision:2};
  const newest=plot.react(gd,[],layout(2));
  sandbox.window.testGate.stop();
  await old; await newest;
  assert.equal(calls.length,1,'older parked frame was replayed');
  assert.equal(calls[0].meta.mattervis_render.render_revision,2,'newest frame did not win');

  // Keep the interaction active and never send pointerup. The bounded replay
  // loop must resolve without invoking Plotly with the parked frame.
  expected={...expected,render_revision:3};
  sandbox.window.testGate.start();
  const parked=plot.react(gd,[],layout(3));
  await parked;
  assert.equal(calls.length,1,'parked frame painted after the retry cap');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_ws_parked_scene_frames_carry_retry_count_and_drop_stale_frames():
    """Scene handoff retries are bounded and cannot replay an older revision."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
let expected={scene_id:'a',render_revision:1,server_started_at:'epoch'};
let timers=[], calls=[];
function later(fn) { timers.push(fn); return timers.length; }
function cancel(id) { if (id>0 && id<=timers.length) timers[id-1]=null; }
function drain() { let fn; while (timers.length && (fn=timers.shift()) !== undefined) if (fn) fn(); }
function figure(rev, scene) {
  return {data:[{type:'scatter3d',x:[rev],y:[0],z:[0]}],layout:{scene:{},meta:{mattervis_render:{scene_id:scene,render_revision:rev,server_started_at:'epoch'}}}};
}
const gd={layout:figure(0,'a').layout};
function react(target, data, layout) { calls.push(layout); target.layout=layout; return Promise.resolve(target); }
let socket;
function WebSocketMock() { socket=this; this.addEventListener=(name, fn)=>{this[name]=fn;}; }
const document={readyState:'loading',addEventListener(){},addEventListener(){},
  documentElement:{},
  getElementById(id){
    if(id==='fast-view-metadata') return {get textContent(){return JSON.stringify(expected);}};
    if(id==='crystal-graph') return {querySelector:()=>gd};
    return null;
  },querySelectorAll(){return []}};
const MutationObserver=function(){}; MutationObserver.prototype.observe=function(){};
const sandbox={console,Promise,JSON,Number,Date,Object,Array,String,Math,setTimeout:later,clearTimeout:cancel,
  MutationObserver,requestAnimationFrame:()=>0,document,WebSocket:WebSocketMock,
  window:{Plotly:{react,newPlot:react,update:react,relayout:react,restyle:react},WebSocket:WebSocketMock,
    location:{protocol:'http:',host:'localhost'},addEventListener(){}}};
vm.createContext(sandbox);
const end=source.indexOf('/* Live compass overlay');
vm.runInContext(source.slice(0,end),sandbox);
function send(p) { socket.message({data:JSON.stringify(p)}); }
const frame=(rev,scene)=>Object.assign({scene_id:scene,render_revision:rev}, {figure:figure(rev,scene)});
(async()=>{
send(frame(2,'b'));
// The selected scene is still A. Every replay must consume one attempt; the
// frame is dropped at the cap instead of spinning forever.
drain();
assert.equal(calls.length,0,'non-selected frame painted during retry loop');
assert.ok(timers.length<=1,'retry loop did not terminate at its cap');

// A queued old revision is discarded as soon as the canvas reports a newer
// revision for that scene, even if the tab handoff timer is still pending.
timers=[]; expected={scene_id:'a',render_revision:1,server_started_at:'epoch'}; gd.layout=figure(0,'a').layout;
send(frame(2,'b'));
expected={scene_id:'b',render_revision:3,server_started_at:'epoch'}; gd.layout=figure(3,'b').layout;
drain();
assert.equal(calls.length,0,'older parked frame overwrote newer canvas revision');

// A current parked frame is delivered once the selected-scene metadata moves
// to the same scene/revision.
timers=[]; expected={scene_id:'a',render_revision:1,server_started_at:'epoch'}; gd.layout=figure(0,'a').layout;
send(frame(4,'b')); expected={scene_id:'b',render_revision:4,server_started_at:'epoch'}; drain();
await Promise.resolve();
assert.equal(calls.length,1,'current parked frame was not delivered');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr

def test_ws_slow_handoff_gets_full_settle_budget():
    """A handoff that uses seven retries still replays after a stale Dash paint."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
let expected={scene_id:'a',render_revision:0,server_started_at:'epoch'};
let timers=[], calls=[];
function later(fn) { timers.push(fn); return timers.length; }
function cancel(id) { if (id>0 && id<=timers.length) timers[id-1]=null; }
function one() { const fn=timers.shift(); if (fn) fn(); }
function figure(rev, scene) {
  return {data:[{type:'scatter3d',x:[rev],y:[0],z:[0]}],
    layout:{scene:{},meta:{mattervis_render:{scene_id:scene,render_revision:rev,server_started_at:'epoch'}}}};
}
const gd={layout:figure(0,'a').layout,querySelectorAll:()=>[]};
function react(target, data, layout) { calls.push(layout); target.layout=layout; return Promise.resolve(target); }
let socket;
function WebSocketMock() { socket=this; this.addEventListener=(name, fn)=>{this[name]=fn;}; }
const document={readyState:'loading',addEventListener(){},
  documentElement:{},
  getElementById(id){
    if(id==='fast-view-metadata') return {get textContent(){return JSON.stringify(expected);}};
    if(id==='crystal-graph') return {querySelector:()=>gd,querySelectorAll:()=>[]};
    return null;
  },querySelectorAll(){return []}};
const MutationObserver=function(){}; MutationObserver.prototype.observe=function(){};
const sandbox={console,Promise,JSON,Number,Date,Object,Array,String,Math,setTimeout:later,clearTimeout:cancel,
  MutationObserver,requestAnimationFrame:()=>0,document,WebSocket:WebSocketMock,
  window:{Plotly:{react,newPlot:react,update:react,relayout:react,restyle:react},WebSocket:WebSocketMock,
    location:{protocol:'http:',host:'localhost'},addEventListener(){}}};
vm.createContext(sandbox);
const end=source.indexOf('/* Live compass overlay');
vm.runInContext(source.slice(0,end),sandbox);
function send(p) { socket.message({data:JSON.stringify(p)}); }
const frame=(rev,scene)=>Object.assign({scene_id:scene,render_revision:rev}, {figure:figure(rev,scene)});
const tick=async()=>{ for (let k=0;k<20;k++) await Promise.resolve(); };
async function drainAsync() {
  let n=0;
  while (timers.length && n++<200) { one(); await tick(); }
}
(async()=>{
send(frame(2,'b'));
// Let the scene handoff consume seven retries while A remains selected.
for (let i=0;i<7;i++) { one(); await tick(); }
expected={scene_id:'b',render_revision:2,server_started_at:'epoch'};
one(); // first eligible handoff paints B and starts a fresh settle budget
await tick();
// Dash repaints stale A after the handoff; settle must replay B.
gd.layout=figure(0,'a').layout;
await drainAsync();
assert.ok(calls.length >= 2,'slow handoff did not replay after stale Dash repaint');
const last=calls[calls.length-1].meta.mattervis_render;
assert.equal(last.scene_id,'b');
assert.equal(last.render_revision,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_ws_parked_replay_reenters_typed_submitter():
    """A parked frame replay must use the typed submitter's ordering fence."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
let expected={scene_id:'a',render_revision:1,server_started_at:'epoch'};
let timers=[], typedCalls=0, directCalls=0;
function later(fn) { timers.push(fn); return timers.length; }
function cancel(id) { if (id>0 && id<=timers.length) timers[id-1]=null; }
function drainOne() { const fn=timers.shift(); if (fn) fn(); }
function figure(rev, scene) {
  return {data:[{type:'scatter3d',x:[rev],y:[0],z:[0]}],
    layout:{scene:{},meta:{mattervis_render:{scene_id:scene,render_revision:rev,server_started_at:'epoch'}}}};
}
const gd={layout:figure(0,'a').layout};
function react(target, data, layout) { directCalls += 1; target.layout=layout; return Promise.resolve(target); }
let socket;
function WebSocketMock() { socket=this; this.addEventListener=(name, fn)=>{this[name]=fn;}; }
const typed={submit(update) {
  typedCalls += 1;
  assert.equal(update.figure.layout.meta.mattervis_render.scene_id,'b');
  // Force one stale result so replay must retry through this same API.
  return typedCalls === 1 ? 'stale' : Promise.resolve('figure');
}};
const document={readyState:'loading',addEventListener(){},documentElement:{},
  getElementById(id){
    if(id==='fast-view-metadata') return {get textContent(){return JSON.stringify(expected);}};
    if(id==='crystal-graph') return {querySelector:()=>gd,querySelectorAll:()=>[]};
    return null;
  },querySelectorAll(){return []}};
const MutationObserver=function(){}; MutationObserver.prototype.observe=function(){};
const sandbox={console,Promise,JSON,Number,Date,Object,Array,String,Math,setTimeout:later,clearTimeout:cancel,
  MutationObserver,requestAnimationFrame:()=>0,document,WebSocket:WebSocketMock,
  window:{Plotly:{react,newPlot:react,update:react,relayout:react,restyle:react},WebSocket:WebSocketMock,
    MatterVisViewUpdates:typed,location:{protocol:'http:',host:'localhost'},addEventListener(){}}};
vm.createContext(sandbox);
const end=source.indexOf('/* Live compass overlay');
vm.runInContext(source.slice(0,end),sandbox);
function send(p) { socket.message({data:JSON.stringify(p)}); }
const frame=(rev,scene)=>Object.assign({scene_id:scene,render_revision:rev}, {figure:figure(rev,scene)});
(async()=>{
  send(frame(2,'b'));
  // The frame is parked while scene A remains selected.
  assert.equal(typedCalls,0);
  expected={scene_id:'b',render_revision:2,server_started_at:'epoch'};
  // At B's selection barrier the typed submitter reports stale once; the
  // bounded retry must invoke it again instead of painting directly.
  drainOne();
  drainOne();
  assert.ok(typedCalls>=1,'parked replay bypassed typed submitter');
  while (timers.length && typedCalls<2) drainOne();
  await Promise.resolve();
  assert.ok(typedCalls>=2,'stale typed replay was not retried');
  assert.equal(directCalls,0,'parked replay bypassed typed submitter with Plotly.react');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_same_scene_id_new_generation_is_not_dropped_as_an_older_frame():
    """Undo can restore an id with a new generation and render revision zero."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const start=source.indexOf('  function scheduleQueuedSceneFigureRetry');
const stop=source.indexOf('  function flushPendingFigurePush',start);
const body=source.slice(start,stop);
const timers=[],pending=Object.create(null),gd={layout:{meta:{mattervis_render:{scene_id:'same',scene_generation:4,render_revision:8,display_version:8,server_started_at:'epoch'}}}};
const sandbox={window:{MatterVisViewUpdates:{submit(frame){ sandbox.typedCalls += 1; return 'figure'; }}},pending,gd,timers,typedCalls:0,
  setTimeout:(fn)=>{timers.push(fn);return timers.length;},clearTimeout:()=>{}};
vm.createContext(sandbox);
vm.runInContext(`
  let current='same';
  function graphDiv(){return gd;}
  function renderMetadataFromLayout(layout){return layout&&layout.meta&&layout.meta.mattervis_render||null;}
  function currentSceneId(){return current;}
  function figurePushIsCurrent(frame){return frame.isCurrent();}
  const pendingFigureRetryTimers=Object.create(null);
  const pendingFigureByScene=pending;
  ${body}
  pending.same={figure:{data:[{}],layout:{meta:{mattervis_render:{scene_id:'same',scene_generation:8,render_revision:0,display_version:0,server_started_at:'epoch'}}}},figure_seq:20,isCurrent:()=>true};
  flushQueuedSceneFigure('same');
`,sandbox);
assert.equal(sandbox.typedCalls,1,'new-generation same-id frame was dropped before typed submitter');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr
