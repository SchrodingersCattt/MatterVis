"""A projection change must not share a gl3d react with a new axis range.

Plotly.js 4.1.1 redraws the previous glplot from Scene.setViewport before
Scene.plot updates dataScale. That redraw's computeTickMarks writes
``glRange / dataScale`` back into the incoming axis ranges. The clip box
then uses the previous ranges and the new scale, which can hide every mesh.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "frontend" / "assets" / "mattervis.js"


def _node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    return node


def test_full_frame_splits_projection_from_axis_range_replacement():
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const small = [[2, 3], [4, 6], [8, 9]];
const wide = [[-4, 12], [1, 2], [-8, 8]];
let selected = 'scene-a';
const reacts = [];
const relayouts = [];
const forwarded = [];
let queueRunningAtRelayout = null;
let allowFlagAtRelayout = null;
let settled = false;
let divertAfterReact = false;
let followUps = 0;
function rangesOf(scene) {
  return ['xaxis', 'yaxis', 'zaxis'].map((name) => scene[name].range.map(Number));
}
function layout(sceneId, ranges, projection, revision) {
  return {
    uirevision: sceneId + '__0',
    scene: {
      camera: {
        eye: {x: 1.6, y: 1.4, z: 1.2},
        center: {x: 0, y: 0, z: 0},
        up: {x: 0, y: 0, z: 1},
        projection: {type: projection},
      },
      xaxis: {range: ranges[0].slice()},
      yaxis: {range: ranges[1].slice()},
      zaxis: {range: ranges[2].slice()},
    },
    meta: {mattervis_render: {
      scene_id: sceneId, render_revision: revision, camera_revision: 1, server_started_at: 'epoch',
    }},
  };
}
function makeGraph(ranges, projection) {
  const gd = {
    layout: layout('scene-a', ranges, projection, 1),
    emit(name, data) { forwarded.push({name, data}); },
  };
  gd._fullLayout = {scene: {_scene: {
    glplot: {canvas: {}},
    camera: {_ortho: projection === 'orthographic'},
    getCamera() {
      const ortho = gd._fullLayout.scene._scene.camera._ortho;
      return {
        eye: {x: 1.6, y: 1.4, z: 1.2}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1},
        projection: {type: ortho ? 'orthographic' : 'perspective'},
      };
    },
  }}};
  gd._fullLayout.scene.xaxis = {range: ranges[0].slice()};
  gd._fullLayout.scene.yaxis = {range: ranges[1].slice()};
  gd._fullLayout.scene.zaxis = {range: ranges[2].slice()};
  return gd;
}
function react(target, data, lay) {
  const next = Array.isArray(data) ? lay : data.layout;
  const scene = target._fullLayout.scene._scene;
  const live = scene.camera._ortho ? 'orthographic' : 'perspective';
  const incoming = next.scene.camera.projection.type;
  const liveRanges = rangesOf(target._fullLayout.scene);
  const incomingRanges = rangesOf(next.scene);
  const changedRange = liveRanges.some((range, index) => range[0] !== incomingRanges[index][0] || range[1] !== incomingRanges[index][1]);
  reacts.push({projection: incoming, ranges: incomingRanges, scene: next.meta.mattervis_render.scene_id, coupled: live !== incoming && changedRange});
  if (typeof target.emit === 'function') {
    target.emit('plotly_relayout', {'scene.camera.projection.type': incoming});
    target.emit('plotly_afterplot', {});
  }
  if (divertAfterReact) selected = 'scene-z';
  target.layout = next;
  scene.camera._ortho = incoming === 'orthographic';
  ['xaxis', 'yaxis', 'zaxis'].forEach((name) => {
    target._fullLayout.scene[name] = {range: next.scene[name].range.slice()};
  });
  return Promise.resolve(target);
}
function relayout(target, patch) {
  queueRunningAtRelayout = !!(target.__mvRenderQueueState && target.__mvRenderQueueState.running);
  allowFlagAtRelayout = !!sandbox.window.__mvAllowCameraRelayout;
  followUps += 1;
  if (followUps === 1) assert.equal(settled, false, 'projection relayout waited until the frame task finished');
  relayouts.push(patch);
  const type = patch['scene.camera.projection.type'];
  if (type && target.layout && target.layout.scene && target.layout.scene.camera) {
    target.layout.scene.camera.projection = {type};
    target._fullLayout.scene._scene.camera._ortho = type === 'orthographic';
  }
  return Promise.resolve(target);
}
const gd = makeGraph(small, 'orthographic');
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, setTimeout, clearTimeout,
  window: {Plotly: {react, newPlot: react, relayout}},
  document: {getElementById(id) {
    if (id === 'fast-view-metadata') {
      return {textContent: JSON.stringify({scene_id: selected, render_revision: 2, server_started_at: 'epoch'})};
    }
    if (id === 'crystal-graph') return {querySelector: () => gd};
    if (id === 'mv-render-status') return {hidden: true, textContent: '', setAttribute() {}, getAttribute: () => ''};
    return null;
  }},
};
vm.createContext(sandbox);
const end = source.indexOf('  // ── Right-click menu');
vm.runInContext(source.slice(0, end) + '\n})();', sandbox);
const start = source.indexOf('  function displayedSceneCamera(');
const stop = source.indexOf('  function redrawCompass(', start);
vm.runInContext(source.slice(start, stop), sandbox);
const plot = sandbox.window.Plotly;
let revision = 1;
function frame(sceneId, ranges, projection) {
  selected = sceneId;
  revision += 1;
  return layout(sceneId, ranges, projection, revision);
}
(async () => {
  const first = plot.react(gd, [], frame('scene-b', wide, 'perspective'));
  const done = first.then(() => { settled = true; });
  await done;
  assert.equal(settled, true);
  assert.equal(reacts.length, 1, 'geometry was not delivered once');
  assert.equal(reacts[0].coupled, false, 'one react changed projection and axis ranges together');
  assert.equal(reacts[0].projection, 'orthographic', 'geometry react did not keep the live projection');
  assert.deepEqual(reacts[0].ranges, wide);
  assert.equal(relayouts.length, 1, 'requested projection was not applied');
  assert.equal(relayouts[0]['scene.camera.projection.type'], 'perspective');
  assert.equal(queueRunningAtRelayout, true, 'projection relayout left the full-frame task');
  assert.equal(allowFlagAtRelayout, false, 'projection relayout reentered the wrapped relayout');
  assert.equal(gd._fullLayout.scene._scene.camera._ortho, false);
  assert.equal(gd.__mvSceneCamera.projection.type, 'perspective');
  assert.equal(gd.__mvSceneCameraOwner, 'scene-b');
  assert.ok(gd.__mvDeliveredFrame, 'frame was marked delivered before the projection settled');
  assert.ok(forwarded.some((event) => event.name === 'plotly_afterplot'), 'non-camera plot events were swallowed');
  assert.equal(forwarded.some((event) => event.name === 'plotly_relayout'), false, 'intermediate camera relayout was persisted');

  reacts.length = 0; relayouts.length = 0; forwarded.length = 0;
  await plot.react(gd, [], frame('scene-a', small, 'orthographic'));
  assert.equal(reacts.length, 1);
  assert.equal(reacts[0].coupled, false);
  assert.equal(reacts[0].projection, 'perspective', 'return trip did not keep the live projection');
  assert.deepEqual(reacts[0].ranges, small);
  assert.equal(relayouts[0]['scene.camera.projection.type'], 'orthographic');
  assert.equal(gd.__mvSceneCamera.projection.type, 'orthographic');

  reacts.length = 0; relayouts.length = 0;
  await plot.react(gd, [], frame('scene-a', wide, 'orthographic'));
  assert.equal(reacts.length, 1, 'same projection was split');
  assert.equal(reacts[0].projection, 'orthographic');
  assert.equal(relayouts.length, 0, 'same projection scheduled a projection relayout');

  reacts.length = 0; relayouts.length = 0;
  await plot.react(gd, [], frame('scene-a', wide, 'perspective'));
  assert.equal(reacts.length, 1, 'matching ranges were split');
  assert.equal(reacts[0].projection, 'perspective', 'commanded projection was dropped when ranges matched');
  assert.equal(relayouts.length, 0);

  const bare = makeGraph(small, 'orthographic');
  delete bare._fullLayout.scene._scene.glplot;
  reacts.length = 0; relayouts.length = 0;
  await plot.react(bare, {data: [], layout: frame('scene-c', wide, 'perspective')});
  assert.equal(reacts.at(-1).projection, 'perspective', 'a new gl scene was split');
  assert.equal(relayouts.length, 0);

  reacts.length = 0; relayouts.length = 0;
  const staleGraph = makeGraph(small, 'orthographic');
  divertAfterReact = true;
  const stale = await plot.react(staleGraph, [], frame('scene-b', wide, 'perspective'));
  divertAfterReact = false;
  selected = 'scene-a';
  assert.equal(relayouts.length, 0, 'a scene change during the geometry draw still changed projection');
  assert.equal(staleGraph.__mvDeliveredFrame, undefined, 'an abandoned projection was marked delivered');
  assert.equal(staleGraph.__mvGraphGeneration, 1, 'a mounted geometry draw left the previous graph generation');
  assert.equal(staleGraph.__mvSceneCamera, undefined, 'an abandoned projection stored a scene camera');
  assert.equal(stale, staleGraph);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([_node(), "-e", program, str(SOURCE)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout


def test_projection_split_restores_emit_and_reads_the_gl_projection():
    """Rejection, a skipped follow-up, and Plotly's omitted-projection default."""
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const small = [[2, 3], [4, 6], [8, 9]];
const wide = [[-4, 12], [1, 2], [-8, 8]];
let selected = 'scene-a';
let rejectReact = false;
let rejectRelayout = false;
const reacts = [];
const relayouts = [];
const forwarded = [];
const hooks = {loss: 0, wheel: 0, refit: 0};
const status = {
  hidden: true, textContent: '', attrs: {},
  setAttribute(name, value) { this.attrs[name] = value; },
  getAttribute(name) { return this.attrs[name] || ''; },
};
function layout(sceneId, ranges, projection, revision) {
  const scene = {
    xaxis: {range: ranges[0].slice()},
    yaxis: {range: ranges[1].slice()},
    zaxis: {range: ranges[2].slice()},
  };
  if (projection !== undefined) {
    scene.camera = {
      eye: {x: 1.6, y: 1.4, z: 1.2},
      center: {x: 0, y: 0, z: 0},
      up: {x: 0, y: 0, z: 1},
    };
    if (projection !== null) scene.camera.projection = {type: projection};
  }
  return {
    uirevision: sceneId + '__0',
    scene,
    meta: {mattervis_render: {
      scene_id: sceneId, render_revision: revision, camera_revision: 1, server_started_at: 'epoch',
    }},
  };
}
function makeGraph(ranges, ortho, reported) {
  const gd = {
    layout: layout('scene-a', ranges, reported, 1),
    emit(name, data) { forwarded.push({name, data}); },
  };
  gd._fullLayout = {scene: {_scene: {
    glplot: {canvas: {id: 'live'}},
    camera: {_ortho: ortho},
    getCamera() {
      return {
        eye: {x: 1.6, y: 1.4, z: 1.2}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1},
        projection: {type: reported},
      };
    },
  }}};
  ['xaxis', 'yaxis', 'zaxis'].forEach((name, index) => {
    gd._fullLayout.scene[name] = {range: ranges[index].slice()};
  });
  return gd;
}
function react(target, data, lay) {
  const next = Array.isArray(data) ? lay : data.layout;
  reacts.push({
    projection: next.scene.camera && next.scene.camera.projection && next.scene.camera.projection.type,
    scene: next.meta.mattervis_render.scene_id,
  });
  if (typeof target.emit === 'function') {
    target.emit('plotly_relayout', {'scene.camera.projection.type': reacts.at(-1).projection || 'perspective'});
  }
  if (rejectReact) return Promise.reject(new Error('react failed'));
  target.layout = next;
  target._fullLayout.scene._scene.camera._ortho = reacts.at(-1).projection === 'orthographic';
  return Promise.resolve(target);
}
function relayout(target, patch) {
  relayouts.push(patch);
  if (rejectRelayout) return Promise.reject(new Error('relayout failed'));
  const type = patch['scene.camera.projection.type'];
  if (type && target._fullLayout) target._fullLayout.scene._scene.camera._ortho = type === 'orthographic';
  return Promise.resolve(target);
}
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, setTimeout, clearTimeout,
  window: {
    Plotly: {react, newPlot: react, relayout},
    MatterVisViewCamera: {
      installContextLoss() { hooks.loss += 1; },
      installWheelZoom() { hooks.wheel += 1; },
      installCanvasRefit() { hooks.refit += 1; },
    },
  },
  document: {getElementById(id) {
    if (id === 'fast-view-metadata') {
      return {textContent: JSON.stringify({scene_id: selected, render_revision: 1, server_started_at: 'epoch'})};
    }
    if (id === 'mv-render-status') return status;
    return null;
  }},
};
vm.createContext(sandbox);
const end = source.indexOf('  // ── Right-click menu');
vm.runInContext(source.slice(0, end) + '\n})();', sandbox);
const start = source.indexOf('  function displayedSceneCamera(');
const stop = source.indexOf('  function redrawCompass(', start);
vm.runInContext(source.slice(start, stop), sandbox);
const plot = sandbox.window.Plotly;
let revision = 1;
function frame(sceneId, ranges, projection) {
  selected = sceneId;
  revision += 1;
  return layout(sceneId, ranges, projection, revision);
}
function cameraEvents() {
  return forwarded.filter((event) => event.name === 'plotly_relayout');
}
(async () => {
  const failedReact = makeGraph(small, true, 'orthographic');
  rejectReact = true;
  await assert.rejects(plot.react(failedReact, [], frame('scene-b', wide, 'perspective')), /react failed/);
  rejectReact = false;
  assert.equal(status.hidden, true, 'busy pill stayed up after a rejected geometry draw');
  assert.equal(status.textContent, '', 'busy text stayed after a rejected geometry draw');
  assert.equal(status.getAttribute('data-level'), 'busy');
  const before = cameraEvents().length;
  failedReact.emit('plotly_relayout', {'scene.camera.projection.type': 'orthographic'});
  assert.equal(cameraEvents().length, before + 1, 'camera relayout stayed swallowed after a rejected react');
  assert.equal(failedReact.__mvGraphGeneration, undefined, 'a rejected react advanced the graph generation');
  assert.equal(hooks.loss, 0, 'context-loss was attached for a rejected geometry draw');

  const failedRelayout = makeGraph(small, true, 'orthographic');
  rejectRelayout = true;
  forwarded.length = 0;
  await assert.rejects(plot.react(failedRelayout, [], frame('scene-b', wide, 'perspective')), /relayout failed/);
  rejectRelayout = false;
  assert.equal(status.hidden, true, 'busy pill stayed up after a rejected projection relayout');
  assert.equal(status.textContent, '');
  failedRelayout.emit('plotly_relayout', {'scene.camera': {projection: {type: 'perspective'}}});
  assert.equal(cameraEvents().length, 1, 'camera relayout stayed swallowed after a rejected projection relayout');

  const staleController = makeGraph(small, true, 'perspective');
  reacts.length = 0; relayouts.length = 0; forwarded.length = 0;
  await plot.react(staleController, [], frame('scene-b', wide, 'orthographic'));
  assert.equal(reacts.length, 1, 'matching gl projection was split');
  assert.equal(reacts[0].projection, 'orthographic', 'stale getCamera projection replaced the live _ortho flag');
  assert.equal(relayouts.length, 0, 'a same-projection frame scheduled a follow-up relayout');
  assert.equal(staleController._fullLayout.scene._scene.camera._ortho, true);

  const omitted = makeGraph(small, true, 'orthographic');
  const lossBefore = hooks.loss;
  reacts.length = 0; relayouts.length = 0;
  const omittedLayout = frame('scene-c', wide, null);
  await plot.react(omitted, [], omittedLayout);
  assert.equal(reacts.length, 1, 'an omitted projection shared the geometry react');
  assert.equal(reacts[0].projection, 'orthographic', 'geometry react did not keep the live orthographic camera');
  assert.equal(relayouts.length, 1, 'omitted projection was not applied as perspective');
  assert.equal(relayouts[0]['scene.camera.projection.type'], 'perspective');
  assert.equal(omitted._fullLayout.scene._scene.camera._ortho, false);
  assert.ok(omitted.__mvSceneCamera, 'the incoming camera was dropped');
  assert.equal(omitted.__mvSceneCamera.projection, undefined, 'the stored camera invented a projection');
  assert.equal(omitted.__mvSceneCamera.eye.x, 1.6);
  assert.equal(hooks.loss, lossBefore + 1, 'projection replacement did not reattach the context-loss listener');
  assert.ok(hooks.wheel >= 1 && hooks.refit >= 1);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([_node(), "-e", program, str(SOURCE)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout


def test_projection_redraw_order_corrupts_only_mismatched_ranges():
    """Replay the plotly.js 4.1.1 range write against both projection directions."""
    program = r"""
const assert = require('node:assert/strict');
// Scene.setViewport redraws before Scene.plot assigns dataScale.
// computeTickMarks then does: range = axesPixels / scene.dataScale.
function tickRanges(layout, dataScale, axesPixels) {
  return ['xaxis', 'yaxis', 'zaxis'].map((name, axis) => {
    const range = layout[name].range;
    range[0] = axesPixels[axis].lo / dataScale[axis];
    range[1] = axesPixels[axis].hi / dataScale[axis];
    return range.slice();
  });
}
function pixels(ranges, scale) {
  return ranges.map((range, axis) => ({lo: range[0] * scale[axis], hi: range[1] * scale[axis]}));
}
function scaleOf(ranges) {
  return ranges.map((range) => 1 / (range[1] - range[0]));
}
function bounds(ranges, scale) {
  return [0, 1].map((end) => ranges.map((range, axis) => range[end] * scale[axis]));
}
function run(oldRanges, newRanges, oldProjection, newProjection) {
  const oldScale = scaleOf(oldRanges);
  const newScale = scaleOf(newRanges);
  const oldPixels = pixels(oldRanges, oldScale);
  const names = ['xaxis', 'yaxis', 'zaxis'];
  const coupled = {xaxis: {range: newRanges[0].slice()}, yaxis: {range: newRanges[1].slice()}, zaxis: {range: newRanges[2].slice()}};
  let calls = [];
  if (oldProjection !== newProjection) {
    calls.push('redraw-before-dataScale');
    tickRanges(coupled, oldScale, oldPixels);
  }
  const coupledRanges = names.map((name) => coupled[name].range.slice());
  const split = {xaxis: {range: newRanges[0].slice()}, yaxis: {range: newRanges[1].slice()}, zaxis: {range: newRanges[2].slice()}};
  const settledPixels = pixels(newRanges, newScale);
  if (oldProjection !== newProjection) {
    calls.push('redraw-after-matching-scale');
    tickRanges(split, newScale, settledPixels);
  }
  return {
    calls,
    coupledBounds: bounds(coupledRanges, newScale),
    splitRanges: names.map((name) => split[name].range.slice()),
    splitBounds: bounds(names.map((name) => split[name].range), newScale),
    expectedBounds: bounds(newRanges, newScale),
  };
}
function maxError(left, right) {
  let error = 0;
  for (let end = 0; end < 2; end += 1) {
    for (let axis = 0; axis < 3; axis += 1) error = Math.max(error, Math.abs(left[end][axis] - right[end][axis]));
  }
  return error;
}
const compact = [[2, 3], [4, 6], [8, 9]];
const wide = [[-4, 12], [1, 2], [-8, 8]];
for (const [oldRanges, newRanges] of [[compact, wide], [wide, compact]]) {
  for (const [before, after] of [['orthographic', 'perspective'], ['perspective', 'orthographic']]) {
    const result = run(oldRanges, newRanges, before, after);
    assert.deepEqual(result.calls, ['redraw-before-dataScale', 'redraw-after-matching-scale']);
    assert.ok(maxError(result.coupledBounds, result.expectedBounds) > 0.5, 'coupled redraw did not move the clip box');
    assert.deepEqual(result.splitRanges, newRanges);
    assert.equal(maxError(result.splitBounds, result.expectedBounds), 0);
  }
}
const same = run(compact, compact, 'orthographic', 'perspective');
assert.equal(maxError(same.coupledBounds, same.expectedBounds), 0, 'equal ranges were treated as corrupted');
"""
    result = subprocess.run([_node(), "-e", program], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr + result.stdout
