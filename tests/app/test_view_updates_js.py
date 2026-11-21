"""Regression coverage for the browser-side view update handoff."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_geometry_notification_does_not_consume_completed_figure_key():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const calls = [];
const gd = {layout: {meta: {mattervis_render: {scene_id: 'scene-a', geometry_version: 3, display_version: 4, camera_version: 0}}}, data: []};
const sandbox = {
  console, Promise, Number, String, Object, Array,
  window: {
    MatterVisViewCamera: {graph: () => gd},
    MatterVisViewCompass: {setEnabled: () => true},
    Plotly: {react: (gd, data, layout) => { gd.data = data; gd.layout = layout; calls.push({data, layout}); return Promise.resolve(); }},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
(async () => {
  const pending = {
    type: 'view_update', update_kind: 'geometry', scene_id: 'scene-a',
    geometry_version: 4, display_version: 5, camera_version: 0,
    payload: {display_options: []},
  };
  assert.equal(submit(pending), '', 'pending geometry notification should wait for its figure');
  const figure = {
    type: 'figure', scene_id: 'scene-a', update_kind: 'geometry',
    geometry_version: 4, display_version: 5, camera_version: 0,
    figure: {data: [{type: 'scatter3d'}], layout: {scene: {}}},
  };
  await submit(figure);
  assert.equal(calls.length, 1, 'completed geometry figure was dropped as a duplicate');
  assert.equal(submit(figure), 'stale', 'successful figure was not deduplicated');
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


def test_out_of_order_display_and_camera_updates_are_rejected():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const calls = [];
const gd = {layout: {meta: {mattervis_render: {
  scene_id: 'scene-a', geometry_version: 3, display_version: 8, camera_version: 5
}}}, data: []};
const sandbox = {
  console, Promise, Number, String, Object, Array,
  window: {
    MatterVisViewCamera: {graph: () => gd},
    MatterVisViewCompass: {setEnabled: () => true},
    Plotly: {relayout: (_gd, payload) => {calls.push(payload); return Promise.resolve();}},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
assert.equal(submit({type:'view_update', update_kind:'display', scene_id:'scene-a',
  geometry_version:3, display_version:7, camera_version:5,
  payload:{display_options:['labels']}}), 'stale');
assert.equal(submit({type:'view_update', update_kind:'camera', scene_id:'scene-a',
  geometry_version:3, display_version:8, camera_version:4,
  payload:{camera:{eye:{x:9}}}}), 'stale');
assert.equal(calls.length, 0);
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_failed_camera_ack_is_retryable_and_snapshots_payload():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {layout: {meta: {mattervis_render: {scene_id: 'scene-a', geometry_version: 3, display_version: 4, camera_version: 0}}}, data: []};
let fail = true, seen = [];
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON,
  window: {
    MatterVisViewCamera: {graph: () => gd, applyAsync: camera => { seen.push(camera); return fail ? Promise.reject(new Error('relayout failed')) : Promise.resolve(true); }},
    Plotly: {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
(async () => {
  const update = {type: 'view_update', update_kind: 'camera', scene_id: 'scene-a', geometry_version: 3, display_version: 4, camera_version: 1, payload: {camera: {eye: {x: 1}}}};
  const first = submit(update);
  update.payload.camera.eye.x = 9;
  await assert.rejects(first, /relayout failed/);
  fail = false;
  assert.equal(await submit(update), 'camera');
  assert.equal(seen[0].eye.x, 1, 'queued camera was not snapshotted');
  assert.equal(seen.length, 2, 'failed camera was not retried');
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


def test_camera_revisions_are_monotonic_and_execution_checks_scene_generation():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    updates = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    camera = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const updates=fs.readFileSync(process.argv[1],'utf8'), camera=fs.readFileSync(process.argv[2],'utf8');
const gd={layout:{scene:{camera:{projection:{type:'perspective'}}},meta:{mattervis_render:{scene_id:'a',scene_generation:1,camera_version:0,geometry_version:1,display_version:1}}},data:[{type:'scatter3d',x:[0],y:[0],z:[0]}]};
let calls=[], release;
const sandbox={console,Promise,Number,String,Object,Array,JSON,Date,setTimeout,clearTimeout,window:{
  Plotly:{relayout:(_gd,patch)=>{calls.push(patch); if (patch['scene.camera.projection.type']) _gd.layout.scene.camera.projection={type:patch['scene.camera.projection.type']}; return new Promise(resolve=>{release=()=>resolve();});}},
  document:{getElementById:id=>id==='crystal-graph'?{querySelector:()=>gd}:null},
}};
vm.createContext(sandbox); vm.runInContext(camera,sandbox); vm.runInContext(updates,sandbox);
const submit=sandbox.window.MatterVisViewUpdates.submit;
const envelope=(version,type='perspective')=>({type:'view_update',update_kind:'camera',scene_id:'a',scene_generation:1,geometry_version:1,display_version:1,camera_version:version,payload:{camera:{eye:{x:1,y:1,z:1},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type}}}});
(async()=>{
  const newer=submit(envelope(3,'orthographic'));
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.length,1);
  assert.equal(submit(envelope(2)), 'stale', 'older callback was accepted after Reset');
  release();
  await newer;
  assert.equal(calls.length,1);
  const old=submit(envelope(4));
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.length,2);
  gd.layout.meta.mattervis_render.scene_generation=2;
  release();
  await assert.rejects(old,/stale/);
  assert.equal(calls.length,2);
  gd.layout.meta.mattervis_render.scene_generation = 1;
  gd.layout.meta.mattervis_render.scene_id = 'a';
  const blocker = submit(envelope(5,'perspective'));
  await Promise.resolve(); await Promise.resolve();
  assert.equal(calls.length,3);
  const superseded = submit(envelope(6,'orthographic'));
  const supersededResult = superseded.catch(error => error);
  gd.layout.meta.mattervis_render.scene_id = 'b';
  release();
  await assert.rejects(blocker, /stale/);
  assert.match(String(await supersededResult), /stale/);
  assert.equal(calls.length, 3, 'scene A camera was applied after the graph became scene B');
  gd.layout.meta.mattervis_render.scene_id = 'a';
  gd.layout.meta.mattervis_render.scene_generation = 1;
  const hold = submit(envelope(7,'perspective'));
  await Promise.resolve(); await Promise.resolve();
  const before = calls.length;
  const queuedOld = submit(envelope(8,'orthographic'));
  const queuedOldResult = queuedOld.catch(error => error);
  const queuedNew = submit(envelope(9,'perspective'));
  release();
  await assert.rejects(hold, /stale/);
  for (let i = 0; i < 10 && calls.length < before + 1; i++) await Promise.resolve();
  assert.equal(calls.length, before + 1, 'older queued camera ran after a newer revision');
  release();
  await queuedNew;
  assert.match(String(await queuedOldResult), /stale/);
  assert.equal(calls[calls.length - 1]['scene.camera.projection.type'], 'perspective');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(updates), str(camera)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_camera_versions_restart_with_a_new_scene_generation():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const gd={layout:{scene:{camera:{projection:{type:'perspective'}},},meta:{mattervis_render:{scene_id:'a',scene_generation:1,camera_version:0,geometry_version:1,display_version:1}}},data:[]};
const seen=[];
const sandbox={console,Promise,Number,String,Object,Array,JSON,window:{
  MatterVisViewCamera:{graph:()=>gd,applyAsync:camera=>{seen.push(camera.projection.type); return Promise.resolve(true);}}, Plotly:{},
}};
vm.createContext(sandbox);vm.runInContext(source,sandbox);
(async()=>{
  const submit=sandbox.window.MatterVisViewUpdates.submit;
  const old=({type:'view_update',update_kind:'camera',scene_id:'a',scene_generation:1,geometry_version:1,display_version:1,camera_version:9,payload:{camera:{projection:{type:'perspective'}}}});
  assert.equal(await submit(old),'camera');
  gd.layout.meta.mattervis_render.scene_generation=2;
  gd.layout.meta.mattervis_render.camera_version=0;
  const update=version=>({type:'view_update',update_kind:'camera',scene_id:'a',scene_generation:2,geometry_version:1,display_version:1,camera_version:version,payload:{camera:{projection:{type:'orthographic'}}}});
  assert.equal(await submit(update(1)),'camera');
  assert.deepEqual(seen,['perspective','orthographic']);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_full_frame_keeps_the_incoming_camera():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const calls = [];
const gd = {layout:{scene:{camera:{eye:{x:9},projection:{type:'orthographic'}}}, meta:{mattervis_render:{scene_id:'scene-a', geometry_version:1, display_version:1, camera_version:1}}}, data:[]};
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON,
  window: {
    MatterVisViewCamera: {graph: () => gd},
    mattervisCurrentCamera: () => ({eye:{x:9,y:0,z:0}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}, projection:{type:'orthographic'}}),
    Plotly: {react: (_gd, data, layout) => { calls.push(layout); return Promise.resolve(); }},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
  await sandbox.window.MatterVisViewUpdates.submit({
    type: 'figure', scene_id: 'scene-a', update_kind: 'geometry',
    geometry_version: 2, display_version: 1, camera_version: 2,
    figure: {data: [{type: 'scatter3d', x:[0], y:[0], z:[0]}], layout: {scene: {camera: {eye:{x:1,y:1,z:1}, projection:{type:'perspective'}}}}},
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].scene.camera.eye.x, 1, 'live camera replaced the incoming reset camera');
  assert.equal(calls[0].scene.camera.projection.type, 'perspective');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_complete_frame_for_selected_scene_replaces_the_canvas_scene():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const calls = [];
const gd = {layout:{meta:{mattervis_render:{scene_id:'scene-a', geometry_version:2, display_version:1, camera_version:1}}}, data:[{x:[1]}]};
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON,
  window: {
    MatterVisSelectedScene: () => 'scene-b',
    MatterVisViewCamera: {graph: () => gd},
    Plotly: {react: (gd, data, layout) => { gd.data = data; gd.layout = layout; calls.push({data, layout, scene: layout.meta.mattervis_render.scene_id}); return Promise.resolve(); }},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
  const frame = await sandbox.window.MatterVisViewUpdates.submit({
    type: 'figure', scene_id: 'scene-b', update_kind: 'geometry',
    geometry_version: 1, display_version: 1, camera_version: 1,
    figure: {data: [{type:'scatter3d', x:[0], y:[0], z:[0]}], layout: {meta:{mattervis_render:{scene_id:'scene-b'}}, scene:{camera:{eye:{x:1.2}}}}},
  });
  assert.equal(frame, 'figure');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].scene, 'scene-b');
  assert.equal(gd.layout.meta.mattervis_render.scene_id, 'scene-b');
  const patch = sandbox.window.MatterVisViewUpdates.submit({
    scene_id: 'scene-b', update_kind: 'display', display_version: 2, geometry_version: 1,
    payload: {display_options: []},
  });
  assert.ok(patch instanceof Promise, 'a display patch for the delivered scene was rejected');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_returning_to_a_scene_repaints_a_previously_delivered_frame():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const calls = [];
let selected = 'scene-a';
const gd = {layout:{meta:{mattervis_render:{scene_id:'scene-a', geometry_version:1, display_version:1, camera_version:0}}}, data:[]};
function frame(scene) {
  return {
    type:'figure', scene_id:scene, update_kind:'geometry',
    geometry_version:1, display_version:1, camera_version:0,
    figure:{data:[{type:'scatter3d', x:[0], y:[0], z:[0]}], layout:{meta:{mattervis_render:{scene_id:scene, geometry_version:1, display_version:1, camera_version:0}}, scene:{}}},
  };
}
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON,
  window: {
    MatterVisSelectedScene: () => selected,
    MatterVisViewCamera: {graph: () => gd},
    Plotly: {react(_gd, data, layout) { _gd.data = data; _gd.layout = layout; calls.push(layout.meta.mattervis_render.scene_id); return Promise.resolve(); }},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
  const submit = sandbox.window.MatterVisViewUpdates.submit;
  assert.equal(await submit(frame('scene-a')), 'figure');
  assert.equal(await submit(frame('scene-a')), 'stale');
  selected = 'scene-b';
  assert.equal(await submit(frame('scene-b')), 'figure');
  selected = 'scene-a';
  assert.equal(await submit(frame('scene-a')), 'figure');
  assert.deepEqual(calls, ['scene-a', 'scene-b', 'scene-a']);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_epoch_reset_and_delivered_map_stay_bounded():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {layout:{meta:{mattervis_render:{scene_id:'scene-a', geometry_version:1, display_version:1, camera_version:0}}}, data:[]};
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON,
  window: {
    MatterVisViewCamera: {graph: () => gd},
    Plotly: {react: (gd, data, layout) => { gd.data = data; gd.layout = layout; return Promise.resolve(gd); }},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
(async () => {
  const older = await submit({
    type:'figure', scene_id:'scene-a', update_kind:'geometry', server_started_at:'2020-01-01T00:00:00',
    geometry_version:1, display_version:1, camera_version:0,
    figure:{data:[{type:'scatter3d'}], layout:{scene:{}}},
  });
  assert.equal(older, 'figure');
  const staleEpoch = submit({
    type:'figure', scene_id:'scene-a', update_kind:'geometry', server_started_at:'2019-01-01T00:00:00',
    geometry_version:2, display_version:1, camera_version:0,
    figure:{data:[{type:'scatter3d'}], layout:{scene:{}}},
  });
  assert.equal(staleEpoch, 'stale');
  const restarted = await submit({
    type:'figure', scene_id:'scene-a', update_kind:'geometry', server_started_at:'2024-01-01T00:00:00',
    geometry_version:1, display_version:1, camera_version:0, operation_id:'restart',
    figure:{data:[{type:'scatter3d'}], layout:{scene:{}}},
  });
  assert.equal(restarted, 'figure');
  for (let i = 0; i < 300; i += 1) {
    await submit({
      type:'figure', scene_id:'scene-a', update_kind:'geometry', server_started_at:'2024-01-01T00:00:00',
      geometry_version:1, display_version:1, camera_version:0, operation_id:'op-'+i,
      figure:{data:[{type:'scatter3d'}], layout:{scene:{}}},
    });
  }
  assert.ok(sandbox.window.MatterVisViewUpdates.deliveredCount() <= 256);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


def test_only_mattervis_opens_the_figure_socket():
    assets = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    transport = assets / "view_transport.js"
    assert transport.exists(), "main's shared view transport asset must remain available"
    transport_source = transport.read_text(encoding="utf-8")
    assert "MatterVisViewTransport" in transport_source
    assert "__mattervisFigureSocket" in transport_source
    hits = []
    for path in sorted(assets.glob("*.js")):
        # The restored transport exposes an opt-in connect() helper but does
        # not invoke it at module load.  MatterVis remains the sole default
        # figure-socket owner.
        if path.name == "view_transport.js":
            continue
        text = path.read_text(encoding="utf-8")
        if "/api/v2/ws" in text and "WebSocket" in text:
            hits.append(path.name)
    assert hits == ["mattervis.js"]


def test_camera_envelope_keeps_scene_generation_beside_versions():
    """A projection camera envelope must stamp scene:generation, not an empty scope.

    ``versions`` carries the three clocks only. ``scene_generation`` is a
    sibling. Losing it makes the next relayout fence report the figure's
    older camera revision, and the server rejects the offscreen refit.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {layout: {meta: {mattervis_render: {
  scene_id: 'scene-a', scene_generation: 3, camera_revision: 1, camera_version: 1,
  geometry_version: 1, display_version: 1, server_started_at: '2026-11-09T00:34:11',
}}}, data: []};
const seen = [];
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON,
  window: {
    MatterVisViewCamera: {
      graph: () => gd,
      applyAsync: (camera, options) => {
        seen.push({camera, options});
        const scope = String(options.scene_id || '') + ':' + String(
          options.scene_generation === undefined || options.scene_generation === null ? '' : options.scene_generation
        );
        gd.__mvCameraRevisionScope = scope;
        gd.__mvCameraRevisionEpoch = String(options.server_started_at || '');
        gd.__mvCameraRevision = Number(options.camera_version);
        return Promise.resolve(true);
      },
    },
    Plotly: {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const update = {
  type: 'view_update',
  update_kind: 'camera',
  kind: 'camera',
  operation_id: 'camera',
  scene_id: 'scene-a',
  scene_generation: 3,
  server_started_at: '2026-11-09T00:34:11',
  versions: {geometry_version: 1, display_version: 1, camera_version: 2},
  geometry_version: 1,
  display_version: 1,
  camera_version: 2,
  payload: {
    command: 'projection',
    camera: {
      eye: {x: 1.8, y: 0, z: 0},
      center: {x: 0, y: 0, z: 0},
      up: {x: 0, y: 0, z: 1},
      projection: {type: 'orthographic'},
    },
  },
};
(async () => {
  assert.equal(await sandbox.window.MatterVisViewUpdates.submit(update), 'camera');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].options.scene_generation, 3);
  assert.equal(gd.__mvCameraRevisionScope, 'scene-a:3');
  assert.equal(gd.__mvCameraRevision, 2);
  const render = gd.layout.meta.mattervis_render;
  const scope = String(render.scene_id) + ':' + String(render.scene_generation);
  const liveScope = String(gd.__mvCameraRevisionScope || '');
  const metaRev = Number(render.camera_revision);
  const live = Number(gd.__mvCameraRevision);
  const revision = liveScope === scope && Number.isFinite(live) ? Math.max(live, metaRev) : metaRev;
  assert.equal(revision, 2, 'the next relayout fence used the figure revision');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_revision_zero_is_a_real_camera_revision():
    """The first figure is revision 0. The same revision is stale after Fit."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const gd={layout:{meta:{mattervis_render:{scene_id:'a',scene_generation:1,camera_revision:0,camera_version:0,render_revision:1,geometry_version:1,display_version:1}}},data:[]};
const sandbox={console,Promise,Number,String,Object,Array,JSON,window:{
  MatterVisViewCamera:{graph:()=>gd,applyAsync:()=>Promise.resolve(true)}, Plotly:{},
}};
vm.createContext(sandbox); vm.runInContext(source,sandbox);
const api=sandbox.window.MatterVisViewUpdates;
const frame=(camera)=>({
  type:'view_update', update_kind:'full', scene_id:'a', scene_generation:1,
  geometry_version:1, display_version:1, camera_version:camera, camera_revision:camera,
  figure:{data:[], layout:{meta:{mattervis_render:{scene_id:'a', camera_revision:camera}}}},
});
assert.equal(api.applicable(frame(0), gd), true, 'the initial revision-0 figure was rejected');
const fit={
  type:'view_update', update_kind:'camera', scene_id:'a', scene_generation:1,
  geometry_version:1, display_version:1, camera_version:1,
  payload:{camera:{eye:{x:1,y:1,z:1},center:{x:0,y:0,z:0},up:{x:0,y:0,z:1},projection:{type:'orthographic'}}, command:'fit'},
};
assert.equal(api.submit(fit) instanceof Promise, true);
assert.equal(api.applicable(frame(0), gd), false, 'revision 0 replaced a Fit');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr or result.stdout


def test_fit_for_the_selected_scene_waits_until_that_geometry_is_shown():
    """A parked Fit is applied only when the react actually installs that figure's traces."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const applied = [];
const gd = {layout: {meta: {mattervis_render: {
  scene_id: 'salt', scene_generation: 1, camera_revision: 4, camera_version: 4,
  render_revision: 2, geometry_version: 2, display_version: 2,
}}}, data: [{name: 'salt', type: 'scatter3d', x: [0, 1]}]};
let selected = 'water';
let gate = 'skip';
const sandbox = {
  console, Promise, Number, String, Object, Array, JSON,
  window: {
    MatterVisSelectedScene: () => selected,
    MatterVisViewCamera: {
      graph: () => gd,
      applyAsync: (camera, options) => {
        applied.push({camera, options, traces: (gd.data || []).map(trace => trace.name)});
        return Promise.resolve(true);
      },
    },
    Plotly: {react: (graph, data, layout) => {
      if (gate === 'reject') return Promise.reject(new Error('react failed'));
      if (gate === 'commit') { graph.data = data; graph.layout = layout; }
      return Promise.resolve(graph);
    }},
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const submit = sandbox.window.MatterVisViewUpdates.submit;
const waterRanges = {x: [0, 1.35], y: [0, 1.05], z: [0, 0.56]};
function fit(scene, generation, version, projection, ranges) {
  return {
    type: 'view_update', update_kind: 'camera', scene_id: scene, scene_generation: generation,
    geometry_version: 1, display_version: 1, camera_version: version,
    payload: {
      command: 'fit', ranges,
      camera: {eye: {x: 0.2, y: 0.2, z: 0.2}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1}, projection: {type: projection}},
    },
  };
}
function figure(scene, generation, camera, name) {
  return {
    type: 'figure', update_kind: 'geometry', scene_id: scene, scene_generation: generation,
    geometry_version: 1, display_version: 1, camera_version: camera, camera_revision: camera,
    figure: {data: [{name, type: 'scatter3d', x: [0]}], layout: {meta: {mattervis_render: {
      scene_id: scene, scene_generation: generation, camera_revision: camera,
    }}}},
  };
}
(async () => {
  assert.equal(submit(fit('water', 1, 2, 'orthographic', waterRanges)), 'parked');
  assert.equal(await submit(figure('water', 1, 1, 'water')), 'skipped');
  assert.equal(applied.length, 0, 'a skipped react flushed the fit');
  assert.equal(gd.data[0].name, 'salt');
  assert.equal(gd.layout.meta.mattervis_render.scene_id, 'salt');
  gate = 'reject';
  await assert.rejects(submit(figure('water', 1, 1, 'water')));
  assert.equal(applied.length, 0, 'a rejected react flushed the fit');
  assert.equal(gd.data[0].name, 'salt');
  gate = 'commit';
  assert.equal(await submit(figure('water', 1, 1, 'water')), 'figure');
  assert.equal(applied.length, 1);
  assert.deepEqual(applied[0].traces, ['water']);
  assert.equal(applied[0].camera.projection.type, 'orthographic');
  assert.deepEqual(applied[0].options.ranges, waterRanges);
  selected = 'salt';
  assert.equal(submit(fit('water', 1, 2, 'orthographic', waterRanges)), 'parked');
  selected = 'water';
  assert.equal(await submit(figure('water', 1, 3, 'water-final')), 'figure');
  assert.equal(applied.length, 1, 'a newer frame kept the older fit');
  assert.equal(gd.data[0].name, 'water-final');
  selected = 'salt';
  assert.equal(submit(fit('water', 2, 0, 'perspective', waterRanges)), 'parked');
  assert.equal(submit(fit('water', 1, 9, 'orthographic', {x: [0, 9], y: [0, 9], z: [0, 9]})), 'parked');
  selected = 'water';
  assert.equal(await submit(figure('water', 2, 0, 'water-gen2')), 'figure');
  assert.equal(applied.length, 2);
  assert.deepEqual(applied[1].traces, ['water-gen2']);
  assert.equal(applied[1].options.scene_generation, 2);
  assert.equal(applied[1].options.camera_version, 0);
  selected = 'salt';
  const epochFit = fit('water', 2, 4, 'orthographic', waterRanges);
  epochFit.server_started_at = '2024-01-01T00:00:00';
  assert.equal(submit(epochFit), 'parked');
  selected = 'water';
  const restarted = figure('water', 2, 0, 'water-epoch');
  restarted.server_started_at = '2025-01-01T00:00:00';
  assert.equal(await submit(restarted), 'figure');
  assert.equal(applied.length, 2, 'an old epoch fit survived the new server epoch');
  selected = 'salt';
  assert.equal(submit(fit('water', 2, 1, 'orthographic', waterRanges)), 'parked');
  assert.equal(applied.length, 2, 'leaving the scene applied its fit');
  selected = 'water';
  submit({type: 'view_update', update_kind: 'display', scene_id: 'water', scene_generation: 2,
    geometry_version: 1, display_version: 2, payload: {display_options: []}});
  assert.equal(applied.length, 3);
  assert.deepEqual(applied[2].traces, ['water-epoch']);
  assert.equal(applied[2].options.camera_version, 1);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
