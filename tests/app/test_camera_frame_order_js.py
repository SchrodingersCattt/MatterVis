"""Exercise camera/frame races through the production browser delivery modules."""

import shutil
import subprocess
from pathlib import Path

import pytest


_HARNESS = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const assets = process.argv[1];
const gate = fs.readFileSync(assets + '/mattervis.js', 'utf8');
const cameraSource = fs.readFileSync(assets + '/view_camera.js', 'utf8');
const updatesSource = fs.readFileSync(assets + '/view_updates.js', 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const camera = (type = 'perspective', eye = 1) => ({
  eye: {x: eye, y: 1, z: 2}, center: {x: 0, y: 0, z: 0},
  up: {x: 0, y: 0, z: 1}, projection: {type},
});
function harness(initial = {}) {
  const base = Object.assign({scene_id: 'a', scene_generation: 1,
    render_revision: 1, geometry_version: 1, display_version: 1,
    camera_revision: 1, camera_version: 1,
    server_started_at: '2026-10-25T10:00:00Z'}, initial);
  let expected = clone(base), live = camera(), hold = false, rejectRelayout = false, releaseFrame;
  const frames = [], relayouts = [];
  const frame = (metadata = {}, view = camera()) => ({
    scene: {camera: clone(view), uirevision: 'same_name__1__same_viewport'},
    meta: {mattervis_render: Object.assign({}, base, metadata)},
  });
  const gd = {layout: frame(), data: [],
    _fullLayout: {scene: {_scene: {getCamera: () => live}}}};
  function react(target, data, layout) {
    const incoming = Array.isArray(data) ? layout : data.layout;
    frames.push(clone(incoming));
    function paint() {
      target.layout = clone(incoming);
      live = clone(incoming.scene.camera);
      return target;
    }
    if (hold) {
      hold = false;
      return new Promise(resolve => { releaseFrame = () => resolve(paint()); });
    }
    return Promise.resolve(paint());
  }
  const sandbox = {console, Promise, Object, Array, JSON, Number, String, Date,
    setTimeout, clearTimeout,
    window: {Plotly: {react, newPlot: react, relayout: (target, patch) => {
      relayouts.push(clone(patch));
      if (rejectRelayout) {
        rejectRelayout = false;
        return Promise.reject(new Error('camera relayout failed'));
      }
      for (const part of ['eye', 'center', 'up']) {
        if (patch['scene.camera.' + part]) {
          live[part] = clone(patch['scene.camera.' + part]);
          target.layout.scene.camera[part] = clone(live[part]);
        }
      }
      if (patch['scene.camera.projection.type']) {
        live.projection = {type: patch['scene.camera.projection.type']};
        target.layout.scene.camera.projection = clone(live.projection);
      }
      if (patch['scene.uirevision']) target.layout.scene.uirevision = patch['scene.uirevision'];
      return Promise.resolve(target);
    }}},
    document: {getElementById: id => id === 'crystal-graph' ? {querySelector: () => gd} :
      id === 'fast-view-metadata' ? {get textContent() { return JSON.stringify(expected); }} : null},
  };
  vm.createContext(sandbox);
  // Keep the real frame gate and FIFO, plus the gesture-stamp function its
  // relayout wrapper calls. Unrelated DOM handlers are outside this harness.
  const gateEnd = gate.indexOf('  // ── Right-click menu');
  const stampStart = gate.indexOf('  function refreshSettledGestureStamp(');
  const stampEnd = gate.indexOf('  function stampGestureScene(', stampStart);
  vm.runInContext(gate.slice(0, gateEnd) + gate.slice(stampStart, stampEnd) +
    'window.observeFrameExpectation = value => rememberExpectedRender(value, true);})();', sandbox);
  const liveStart = gate.indexOf('  function displayedSceneCamera(');
  const liveEnd = gate.indexOf('  function redrawCompass(', liveStart);
  vm.runInContext(gate.slice(liveStart, liveEnd), sandbox);
  vm.runInContext(cameraSource, sandbox);
  vm.runInContext(updatesSource, sandbox);
  const root = sandbox.window;
  return {
    gd, frames, relayouts, frame, root,
    live: () => clone(live),
    drag: view => { live = clone(view); },
    holdNextFrame: () => { hold = true; },
    rejectNextRelayout: () => { rejectRelayout = true; },
    frameStarted: () => typeof releaseFrame === 'function',
    releaseFrame: () => releaseFrame(),
    expect: metadata => {
      expected = clone(metadata);
      root.observeFrameExpectation(expected);
    },
    submitFrame: (layout, signature = 'positional') => signature === 'object'
      ? root.Plotly.react(gd, {data: [], layout}) : root.Plotly.react(gd, [], layout),
    command: (version, type, eye = 1, command = '') => {
      const shown = gd.layout.meta.mattervis_render;
      return root.MatterVisViewUpdates.submit({type: 'view_update', update_kind: 'camera',
        scene_id: shown.scene_id, scene_generation: shown.scene_generation,
        server_started_at: shown.server_started_at,
        geometry_version: shown.geometry_version, display_version: shown.display_version,
        camera_version: version, payload: {camera: camera(type, eye), command}});
    },
  };
}
async function spinUntil(check) {
  for (let i = 0; i < 40 && !check(); i++) await Promise.resolve();
  assert.ok(check(), 'expected queued task did not start');
}
async function within(work) {
  let timer;
  try {
    return await Promise.race([work, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('camera/frame delivery deadlocked')), 1000);
    })]);
  } finally { clearTimeout(timer); }
}
"""


def _run_node(scenario: str, *args: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    assets = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    program = _HARNESS + "\n(async () => {\n" + scenario + r"""
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(assets), *args],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("signature", ["object", "positional"])
def test_stale_dash_frame_cannot_undo_a_newer_projection(signature):
    _run_node(r"""
  const h = harness();
  assert.equal(await h.command(2, 'orthographic'), 'camera');
  await h.submitFrame(h.frame(), process.argv[2]);
  assert.equal(h.frames.length, 0, 'obsolete frame reached Plotly before metadata caught up');
  assert.equal(h.live().projection.type, 'orthographic');
""", signature)


@pytest.mark.parametrize("signature", ["object", "positional"])
def test_queued_frame_rechecks_the_newer_camera_revision(signature):
    _run_node(r"""
  const h = harness();
  await h.command(1, 'perspective');
  let release;
  const blocker = h.root.MatterVisRenderQueue.enqueue(h.gd,
    () => new Promise(resolve => { release = resolve; }));
  await spinUntil(() => typeof release === 'function');
  const stale = h.submitFrame(h.frame(), process.argv[2]);
  await within(h.command(2, 'orthographic'));
  await within(stale);
  release();
  await within(blocker);
  assert.equal(h.frames.length, 0, 'queued frame was not checked after the camera advanced');
  assert.equal(h.live().projection.type, 'orthographic');
""", signature)


def test_late_running_frame_restores_the_newer_camera_and_releases_fifo():
    _run_node(r"""
  const h = harness();
  await h.command(1, 'perspective');
  h.holdNextFrame();
  const running = h.submitFrame(h.frame({render_revision: 2, geometry_version: 2}));
  await spinUntil(h.frameStarted);
  await within(h.command(2, 'orthographic', 4));
  assert.equal(h.live().projection.type, 'orthographic');
  h.releaseFrame();
  await within(running);
  assert.equal(h.live().projection.type, 'orthographic', 'late full frame undid newer projection');
  assert.equal(h.live().eye.x, 4, 'late full frame undid newer camera eye');
  let laterTaskRan = false;
  await within(h.root.MatterVisRenderQueue.enqueue(h.gd, () => { laterTaskRan = true; }));
  assert.equal(laterTaskRan, true, 'camera restoration blocked the shared FIFO');
""")


def test_new_scene_generation_cannot_reuse_the_previous_dragged_camera():
    _run_node(r"""
  const h = harness();
  h.drag(camera('orthographic', 9));
  const incoming = h.frame({scene_generation: 2}, camera('perspective', 3));
  h.expect(incoming.meta.mattervis_render);
  await h.submitFrame(incoming);
  assert.equal(h.frames.length, 1);
  assert.equal(h.live().eye.x, 3, 'dragged eye leaked across scene generation');
  assert.equal(h.live().projection.type, 'perspective');
""")


@pytest.mark.parametrize("boundary", ["generation", "epoch"])
def test_new_scope_accepts_reset_revisions_and_following_camera_commands(boundary):
    _run_node(r"""
  const h = harness();
  await h.command(7, 'orthographic', 7);
  const changed = process.argv[2] === 'generation' ? {scene_generation: 2} :
    {server_started_at: '2026-10-25T11:00:00Z'};
  const incoming = h.frame(Object.assign({render_revision: 0, geometry_version: 0,
    display_version: 0, camera_revision: 0, camera_version: 0}, changed), camera('perspective', 3));
  h.expect(incoming.meta.mattervis_render);
  await h.submitFrame(incoming);
  assert.equal(h.frames.length, 1, 'old scoped clock rejected a fresh lower-revision frame');
  assert.equal(h.live().eye.x, 3);
  assert.equal(await within(h.command(1, 'orthographic', 5)), 'camera');
  assert.equal(h.live().projection.type, 'orthographic');
  assert.equal(h.live().eye.x, 5, 'old scoped clock rejected a fresh camera command');
""", boundary)


def test_same_generation_frame_keeps_dragged_eye_and_commanded_projection():
    _run_node(r"""
  const h = harness();
  const dragged = camera('perspective', 9);
  dragged.center = {x: 0.3, y: -0.2, z: 0.1};
  dragged.up = {x: 0, y: 1, z: 0};
  h.drag(dragged);
  await h.submitFrame(h.frame({render_revision: 2, geometry_version: 2}, camera('orthographic', 3)));
  assert.deepEqual(h.live().eye, dragged.eye);
  assert.deepEqual(h.live().center, dragged.center);
  assert.deepEqual(h.live().up, dragged.up);
  assert.equal(h.live().projection.type, 'orthographic');
""")


@pytest.mark.parametrize("boundary", ["generation", "epoch"])
def test_rejected_old_scope_command_preserves_the_current_clock_and_snapshot(boundary):
    _run_node(r"""
  const h = harness({scene_generation: 2, server_started_at: '2026-10-25T11:00:00Z'});
  await h.command(3, 'orthographic', 5);
  const latest = h.gd.__mvLatestCamera;
  const before = {revision: h.gd.__mvCameraRevision, scope: h.gd.__mvCameraRevisionScope,
    epoch: h.gd.__mvCameraRevisionEpoch};
  const stale = {scene_id: 'a', scene_generation: 2, camera_version: 99,
    server_started_at: '2026-10-25T11:00:00Z'};
  if (process.argv[2] === 'generation') stale.scene_generation = 1;
  else stale.server_started_at = '2026-10-25T10:00:00Z';
  await assert.rejects(h.root.MatterVisViewCamera.applyAsync(camera('perspective', 99), stale), /stale/);
  assert.deepEqual({revision: h.gd.__mvCameraRevision, scope: h.gd.__mvCameraRevisionScope,
    epoch: h.gd.__mvCameraRevisionEpoch}, before, 'rejected command changed the active scoped clock');
  assert.equal(h.gd.__mvLatestCamera, latest, 'rejected command replaced the active snapshot');
  assert.equal(h.relayouts.length, 1, 'rejected command reached Plotly');
  assert.equal(h.live().projection.type, 'orthographic');
  assert.equal(h.live().eye.x, 5);
""", boundary)


def test_rejected_reset_relayout_recovers_its_eye_through_the_real_frame_gate():
    _run_node(r"""
  const h = harness();
  const previousUi = h.gd.layout.scene.uirevision;
  h.drag(camera('perspective', 9));
  h.rejectNextRelayout();
  assert.equal(await within(h.command(2, 'orthographic', 4, 'reset')), 'camera');
  assert.equal(h.relayouts.length, 1);
  assert.equal(h.frames.length, 1, 'failed relayout did not produce one recovery frame');
  assert.equal(h.root.MatterVisExpectedRender('a').camera_revision, 1,
    'test must recover before fast metadata catches up');
  assert.equal(h.frames[0].meta.mattervis_render.camera_revision, 2,
    'recovery did not carry the current camera clock');
  assert.notEqual(h.frames[0].scene.uirevision, previousUi,
    'recovery reused the camera continuity marker that kept the failed eye');
  assert.equal(h.live().projection.type, 'orthographic');
  assert.equal(h.live().eye.x, 4, 'reset recovery preserved the failed live eye');
  assert.equal(h.root.__mvRenderStatus.level, 'recovered');
  let laterTaskRan = false;
  await within(h.root.MatterVisRenderQueue.enqueue(h.gd, () => { laterTaskRan = true; }));
  assert.equal(laterTaskRan, true, 'reset recovery blocked later render work');
""")
