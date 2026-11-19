"""A blank or superseded figure must not replace the current scene."""

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const gate = fs.readFileSync(process.argv[1] + '/mattervis.js', 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
function harness() {
  const expected = {scene_id: 'scene-a', scene_generation: 1, render_revision: 1,
    geometry_version: 1, display_version: 1, camera_revision: 1, camera_version: 1,
    server_started_at: '2026-10-25T10:00:00Z'};
  const frames = [];
  const gd = {data: [], layout: {scene: {camera: {eye: {x: 1, y: 1, z: 1}}},
    meta: {mattervis_render: clone(expected)}}};
  function react(target, data) {
    const figure = Array.isArray(data) ? {data: data, layout: arguments[2]} : data;
    const traces = Array.isArray(figure.data) ? figure.data : [];
    frames.push({scene: figure.layout.meta.mattervis_render.scene_id,
      revision: figure.layout.meta.mattervis_render.render_revision, traces: traces.length});
    target.layout = clone(figure.layout);
    if (traces.length) target.data = traces;
    return Promise.resolve(target);
  }
  const sandbox = {console, Promise, Object, Array, JSON, Number, String, Date,
    setTimeout, clearTimeout,
    window: {Plotly: {react, newPlot: react, relayout: () => Promise.resolve(gd)}},
    document: {getElementById: id => id === 'crystal-graph' ? {querySelector: () => gd} :
      id === 'fast-view-metadata' ? {get textContent() { return JSON.stringify(expected); }} : null}};
  vm.createContext(sandbox);
  const gateEnd = gate.indexOf('  // ── Right-click menu');
  vm.runInContext(gate.slice(0, gateEnd) + '})();', sandbox);
  const trace = () => [{type: 'scatter3d', mode: 'markers', x: [0], y: [0], z: [0], visible: true}];
  return {frames, gd, expected, root: sandbox.window, trace,
    submit(scene, revision, data) {
      const layout = {scene: {camera: {eye: {x: 1, y: 1, z: 1}},
        uirevision: scene + '__name__' + revision + '__viewport'},
        meta: {mattervis_render: Object.assign({}, expected, {scene_id: scene,
          render_revision: revision, camera_revision: revision, camera_version: revision})}};
      return sandbox.window.Plotly.react(gd, {data: data, layout: layout});
    }};
}
async function spinUntil(check) {
  for (let i = 0; i < 40 && !check(); i++) await Promise.resolve();
  assert.ok(check(), 'expected queued task did not start');
}
"""


def _run_node(scenario: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    assets = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    program = _HARNESS + "\n(async () => {\n" + scenario + r"""
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(assets)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_empty_figure_does_not_clear_drawable_traces():
    _run_node(r"""
  const h = harness();
  h.gd.data = h.trace();
  await h.submit('scene-a', 1, []);
  assert.equal(h.frames.length, 0, 'empty figure reached Plotly');
  assert.equal(h.gd.data.length, 1, 'empty figure cleared the current traces');
  assert.equal(h.gd.data[0].x.length, 1);
""")


def test_duplicate_unchanged_frame_is_not_rebuilt():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  await h.submit('scene-a', 1, h.trace());
  assert.equal(h.frames.length, 1, 'unchanged scene rebuilt the canvas');
  assert.equal(h.frames[0].scene, 'scene-a');
  assert.equal(h.frames[0].traces, 1);
""")


def test_older_revision_does_not_replace_the_current_frame():
    _run_node(r"""
  const h = harness();
  h.expected.render_revision = 4;
  h.expected.camera_revision = 4;
  await h.submit('scene-a', 1, h.trace());
  assert.equal(h.frames.length, 0, 'older revision reached Plotly');
  assert.equal(h.gd.data.length, 0, 'older revision painted over the current scene');
""")


def test_queued_older_frame_yields_to_the_newer_frame_for_the_same_scene():
    _run_node(r"""
  const h = harness();
  let release;
  const blocker = h.root.MatterVisRenderQueue.enqueue(h.gd,
    () => new Promise(resolve => { release = resolve; }));
  await spinUntil(() => typeof release === 'function');
  const older = h.submit('scene-a', 2, h.trace());
  const empty = h.submit('scene-a', 3, []);
  const newer = h.submit('scene-a', 4, h.trace());
  release();
  await older;
  await empty;
  await newer;
  await blocker;
  assert.deepEqual(h.frames.map(frame => frame.revision), [4],
    'a queued older or empty frame still reached Plotly');
  assert.equal(h.gd.data.length, 1);
  assert.equal(h.gd.layout.meta.mattervis_render.render_revision, 4);
""")


def test_scene_switch_keeps_the_final_scene_when_the_previous_frame_is_still_queued():
    _run_node(r"""
  const h = harness();
  let release;
  const blocker = h.root.MatterVisRenderQueue.enqueue(h.gd,
    () => new Promise(resolve => { release = resolve; }));
  await spinUntil(() => typeof release === 'function');
  const previous = h.submit('scene-a', 2, h.trace());
  h.expected.scene_id = 'scene-b';
  h.expected.render_revision = 2;
  h.expected.camera_revision = 2;
  const current = h.submit('scene-b', 2, h.trace());
  const stale = h.submit('scene-a', 1, h.trace());
  release();
  await previous;
  await current;
  await stale;
  await blocker;
  assert.deepEqual(h.frames.map(frame => frame.scene), ['scene-b'],
    'the previous scene painted after the selected scene changed');
  assert.equal(h.gd.layout.meta.mattervis_render.scene_id, 'scene-b');
  assert.equal(h.gd.data.length, 1);
""")
