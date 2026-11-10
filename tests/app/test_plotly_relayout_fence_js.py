"""Foreign-scene drags must not move the canvas that is mounted now."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_foreign_gesture_does_not_relayout_the_mounted_scene():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend/assets/mattervis.js"
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const end = source.indexOf('  function stampGestureScene(');
if (end < 0) throw new Error('gesture stamp not found');
const calls = [];
const status = {hidden: true, textContent: '', attrs: {}, setAttribute(name, value) { this.attrs[name] = value; }};
const gd = {
  layout: {
    scene: {camera: {eye: {x: 1, y: 0, z: 0}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1}}},
    meta: {mattervis_render: {scene_id: 'canvas-b', scene_generation: 7, render_revision: 1, camera_revision: 1, server_started_at: 'epoch'}},
  },
  __mvSceneCamera: {eye: {x: 0, y: 1, z: 0}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1}},
  __mvSceneCameraOwner: 'scene-a',
};
let displayedCamera = null;
function relayout(target, patch) {
  calls.push(patch);
  if (patch && patch.width && displayedCamera) {
    if (displayedCamera.commandDuringSize) target.__mvLatestCamera = displayedCamera.commandDuringSize;
    if (displayedCamera.afterSize) displayedCamera.current = displayedCamera.afterSize;
  }
  return Promise.resolve(target);
}
const document = {
  readyState: 'complete',
  body: null,
  documentElement: null,
  getElementById(id) {
    if (id === 'mv-render-status') return status;
    if (id === 'fast-view-metadata') {
      return {textContent: JSON.stringify({scene_id: 'scene-a', render_revision: 1, server_started_at: 'epoch'})};
    }
    if (id === 'crystal-graph') return {querySelector() { return gd; }};
    return null;
  },
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const sandbox = {
  console, Promise, JSON, Number, Date, Object, Array, String, Math, Set, WeakSet, Map,
  setTimeout() { return 0; },
  clearTimeout() {},
  requestAnimationFrame() { return 1; },
  cancelAnimationFrame() {},
  window: {Plotly: {react() {}, newPlot() {}, update() {}, restyle() {}, relayout}},
  document,
};
sandbox.window.document = document;
vm.createContext(sandbox);
vm.runInContext(source.slice(0, end) + `
window.__mvFenceTest = {
  refresh: refreshSettledGestureStamp,
  mark: markActive,
  release: releaseGestureScene,
  generation: function() { return gestureGeneration; },
  park(id, figure) { pendingFigureByScene[String(id)] = {figure: figure}; },
  parked(id) { return pendingFigureByScene[String(id)]; },
  retry: scheduleQueuedSceneFigureRetry,
};
})();`, sandbox);

const plot = sandbox.window.Plotly;
const fence = sandbox.window.__mvFenceTest;
(async () => {
  sandbox.window.__mvGestureSceneId = 'scene-a';
  sandbox.window.__mvGestureKind = 'pointer';
  sandbox.window.__mvGestureEndedAt = Date.now();
  sandbox.window.__mvGestureSceneGeneration = 1;
  await plot.relayout(gd, {'scene.camera': {eye: {x: 9, y: 9, z: 9}}});
  assert.equal(calls.length, 0, 'foreign drag was applied to the mounted scene');

  calls.length = 0;
  gd.__mvSceneCameraOwner = 'canvas-b';
  await plot.relayout(gd, {'scene.camera': {eye: {x: 9, y: 9, z: 9}}});
  assert.equal(calls.length, 1, 'owned canvas camera was not restored');
  assert.equal(calls[0]['scene.camera'].eye.x, 0);
  assert.equal(calls[0]['scene.camera'].eye.y, 1);

  sandbox.window.__mvGestureSceneId = 'scene-a';
  sandbox.window.__mvGestureSceneGeneration = 1;
  sandbox.window.__mvGestureEndedAt = Date.now() - 1000;
  fence.refresh(gd, {'scene.camera': {eye: {x: 1, y: 0, z: 0}}});
  assert.equal(sandbox.window.__mvGestureSceneGeneration, 1, 'canvas generation was copied onto another scene');

  sandbox.window.__mvGestureSceneId = 'canvas-b';
  fence.refresh(gd, {'scene.camera': {eye: {x: 1, y: 0, z: 0}}});
  assert.equal(sandbox.window.__mvGestureSceneGeneration, 7, 'same-scene gesture did not take the live generation');

  sandbox.window.__mvGestureSceneId = 'canvas-b';
  sandbox.window.__mvGestureKind = 'pointer';
  sandbox.window.__mvGestureEndedAt = 0;
  sandbox.window.__mvGesturePointerRelayoutSeen = false;
  sandbox.window.__mvGestureCameraRevision = 1;
  sandbox.window.__mvGestureSceneGeneration = 4;
  gd.__mvCameraRevision = 2;
  gd.layout.meta.mattervis_render.camera_revision = 1;
  gd.layout.meta.mattervis_render.scene_generation = 7;
  fence.refresh(gd, {'scene.camera': {eye: {x: 1, y: 0, z: 0}}});
  assert.equal(sandbox.window.__mvGestureCameraRevision, 1, 'an in-progress drag kept the gesture revision');
  assert.equal(sandbox.window.__mvGesturePointerRelayoutSeen, false, 'an in-progress drag consumed the pointer-up relayout');
  sandbox.window.__mvGestureEndedAt = Date.now();
  fence.refresh(gd, {'scene.camera': {eye: {x: 1, y: 0, z: 0}}});
  assert.equal(sandbox.window.__mvGestureCameraRevision, 1, 'the pointer-up relayout kept the gesture revision');
  gd.__mvCameraRevisionScope = 'canvas-b:7';
  gd.__mvCameraRevisionEpoch = 'epoch';
  fence.refresh(gd, {'scene.camera': {eye: {x: 0, y: 0, z: 50}}});
  assert.equal(sandbox.window.__mvGestureCameraRevision, 2, 'a later same-scene relayout did not read the live camera clock');
  assert.equal(sandbox.window.__mvGestureSceneGeneration, 7, 'a later same-scene relayout did not take the live generation');

  gd.__mvCameraRevisionScope = 'scene-a:3';
  gd.__mvCameraRevision = 9;
  gd.__mvCameraRevisionEpoch = 'epoch';
  fence.refresh(gd, {'scene.camera': {eye: {x: 0, y: 0, z: 50}}});
  assert.equal(sandbox.window.__mvGestureCameraRevision, 1, 'a later relayout adopted another scene revision');
  assert.equal(sandbox.window.__mvGestureSceneGeneration, 7, 'a foreign revision changed the mounted generation');

  gd.__mvCameraRevisionScope = 'canvas-b:7';
  gd.__mvCameraRevisionEpoch = 'previous-session';
  gd.__mvCameraRevision = 9;
  fence.refresh(gd, {'scene.camera': {eye: {x: 0, y: 0, z: 50}}});
  assert.equal(sandbox.window.__mvGestureCameraRevision, 1, 'a later relayout adopted another session revision');

  const dragged = {eye: {x: 2, y: 0, z: 0}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1}, projection: {type: 'perspective'}};
  const committed = {eye: {x: 1, y: 0, z: 0}, center: {x: 0, y: 0, z: 0}, up: {x: 0, y: 0, z: 1}, projection: {type: 'perspective'}};
  displayedCamera = {current: dragged, afterSize: committed};
  sandbox.window.mattervisCurrentCamera = function () { return displayedCamera.current; };
  gd.emit = function () { return gd; };
  sandbox.window.__mvGestureSceneId = 'canvas-b';
  sandbox.window.__mvGestureKind = 'pointer';
  sandbox.window.__mvGestureEndedAt = 0;
  sandbox.window.__mvGestureSceneGeneration = 7;
  sandbox.window.__mvGestureServerEpoch = 'epoch';
  calls.length = 0;
  await plot.relayout(gd, {width: 800, height: 600});
  assert.equal(calls.length, 2, 'a size replot during a drag did not restore the live camera');
  assert.equal(calls[0].width, 800);
  assert.equal(calls[1]['scene.camera.eye'].x, 2, 'the size replot committed the pre-drag eye');

  sandbox.window.__mvGestureEndedAt = Date.now();
  displayedCamera = {current: dragged, afterSize: committed};
  calls.length = 0;
  await plot.relayout(gd, {width: 640, height: 480});
  assert.equal(calls.length, 1, 'a size replot after pointer-up restored a finished drag');

  sandbox.window.__mvGestureEndedAt = 0;
  sandbox.window.__mvGestureSceneGeneration = 7;
  sandbox.window.__mvGestureServerEpoch = 'epoch';
  gd.__mvLatestCamera = {snapshot: dragged};
  displayedCamera = {current: dragged, afterSize: committed, commandDuringSize: {snapshot: committed}};
  calls.length = 0;
  await plot.relayout(gd, {width: 700, height: 500});
  assert.equal(calls.some(function (patch) { return patch['scene.camera.eye'] && patch['scene.camera.eye'].x === 2; }), false, 'a size replot let the drag overwrite Reset');
  delete gd.__mvLatestCamera;
  displayedCamera = null;

  sandbox.window.__mvGestureEndedAt = 0;
  sandbox.window.__mvGestureSceneId = 'canvas-b';
  sandbox.window.__mvGestureSceneGeneration = 7;
  sandbox.window.__mvGestureServerEpoch = 'epoch';
  gd.layout.meta.mattervis_render.scene_generation = 8;
  displayedCamera = {current: dragged, afterSize: committed};
  calls.length = 0;
  await plot.relayout(gd, {width: 810, height: 610});
  assert.equal(calls.some(function (patch) { return patch['scene.camera.eye'] && patch['scene.camera.eye'].x === 2; }), false, 'a new generation reused the in-progress drag eye');
  gd.layout.meta.mattervis_render.scene_generation = 7;

  sandbox.window.__mvGestureSceneGeneration = 7;
  sandbox.window.__mvGestureServerEpoch = 'epoch';
  gd.layout.meta.mattervis_render.server_started_at = 'restarted';
  displayedCamera = {current: dragged, afterSize: committed};
  calls.length = 0;
  await plot.relayout(gd, {width: 820, height: 620});
  assert.equal(calls.some(function (patch) { return patch['scene.camera.eye'] && patch['scene.camera.eye'].x === 2; }), false, 'a restarted session reused the in-progress drag eye');
  gd.layout.meta.mattervis_render.server_started_at = 'epoch';
  displayedCamera = null;

  function restoredDrag(list) {
    return list.some(function (patch) {
      return patch['scene.camera.eye'] && patch['scene.camera.eye'].x === 2;
    });
  }
  sandbox.window.__mvGestureEndedAt = 0;
  sandbox.window.__mvGestureSceneId = 'canvas-b';
  sandbox.window.__mvGestureSceneGeneration = 7;
  sandbox.window.__mvGestureServerEpoch = 'epoch';
  sandbox.window.__mvGestureCameraRevision = 1;
  gd.layout.meta.mattervis_render.camera_revision = 1;
  displayedCamera = {current: dragged};
  sandbox.window.__mvNoteInProgressDrag(gd);
  displayedCamera = {current: committed};
  calls.length = 0;
  await sandbox.window.__mvRestoreInProgressDrag(gd);
  assert.equal(restoredDrag(calls), true, 'a replot during the drag did not restore the sampled eye');

  gd.layout.meta.mattervis_render.scene_generation = 8;
  calls.length = 0;
  await sandbox.window.__mvRestoreInProgressDrag(gd);
  assert.equal(restoredDrag(calls), false, 'a replot on a new generation restored the previous drag eye');
  gd.layout.meta.mattervis_render.scene_generation = 7;

  gd.layout.meta.mattervis_render.server_started_at = 'restarted';
  calls.length = 0;
  await sandbox.window.__mvRestoreInProgressDrag(gd);
  assert.equal(restoredDrag(calls), false, 'a replot after restart restored the previous drag eye');
  gd.layout.meta.mattervis_render.server_started_at = 'epoch';

  gd.__mvCameraRevisionScope = 'canvas-b:7';
  gd.__mvCameraRevisionEpoch = 'epoch';
  gd.__mvCameraRevision = 4;
  calls.length = 0;
  await sandbox.window.__mvRestoreInProgressDrag(gd);
  assert.equal(restoredDrag(calls), false, 'a newer camera command lost to the in-progress drag');
  delete gd.__mvCameraRevision;
  delete gd.__mvCameraRevisionScope;
  delete gd.__mvCameraRevisionEpoch;
  displayedCamera = null;

  sandbox.window.__mvGestureSceneId = 'scene-a';
  fence.mark();
  const firstGeneration = fence.generation();
  fence.mark();
  fence.release(firstGeneration, 'scene-a');
  assert.equal(sandbox.window.__mvGestureSceneId, 'scene-a', 'a newer drag on the same scene was cleared');
  fence.release(fence.generation(), 'scene-a');
  assert.equal(sandbox.window.__mvGestureSceneId, '', 'the active drag was not released');

  fence.park('scene-a', {data: [], layout: {meta: {mattervis_render: {scene_id: 'scene-a', render_revision: 1}}}});
  fence.retry('scene-a', 120);
  assert.equal(status.textContent, 'Scene frame was not applied');
  assert.equal(status.attrs['data-level'], 'error');
  assert.equal(sandbox.window.__mvRenderStatus.scene_id, 'scene-a');
  assert.equal(fence.parked('scene-a'), undefined);

  fence.park('scene-b', {data: [], layout: {meta: {mattervis_render: {scene_id: 'scene-b', render_revision: 1}}}});
  fence.retry('scene-b', 120);
  assert.equal(fence.parked('scene-b').figure.layout.meta.mattervis_render.scene_id, 'scene-b');
  assert.equal(status.textContent, 'Scene frame was not applied');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    completed = subprocess.run(
        [node, "-e", program, str(source)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
