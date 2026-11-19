"""The frame witness records a decision without choosing the painted frame."""

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const gate = fs.readFileSync(process.argv[1] + '/mattervis.js', 'utf8');
function harness() {
  const expected = {scene_id: 'scene-a', scene_generation: 1, render_revision: 1,
    geometry_version: 1, display_version: 1, camera_revision: 1, camera_version: 1,
    server_started_at: '2026-10-25T10:00:00Z'};
  const frames = [];
  const nodes = {};
  const gd = {data: [], layout: {scene: {camera: {eye: {x: 1, y: 1, z: 1}}},
    meta: {mattervis_render: JSON.parse(JSON.stringify(expected))}}};
  function react(target, data) {
    const figure = Array.isArray(data) ? {data: data, layout: arguments[2]} : data;
    frames.push((figure.data || []).length);
    target.layout = JSON.parse(JSON.stringify(figure.layout));
    target.data = figure.data || [];
    return Promise.resolve(target);
  }
  const document = {
    body: {appendChild(node) { nodes[node.id] = node; }},
    createElement() { return {id: '', hidden: false, textContent: '', setAttribute() {}}; },
    getElementById(id) {
      if (id === 'crystal-graph') return {querySelector: () => gd, appendChild(node) { nodes[node.id] = node; }};
      if (id === 'fast-view-metadata') return {get textContent() { return JSON.stringify(expected); }};
      return nodes[id] || null;
    },
  };
  const sandbox = {console, Promise, Object, Array, JSON, Number, String, Date,
    setTimeout, clearTimeout, document,
    window: {Plotly: {react, newPlot: react, relayout: () => Promise.resolve(gd)}}};
  vm.createContext(sandbox);
  const gateEnd = gate.indexOf('  // ── Right-click menu');
  vm.runInContext(gate.slice(0, gateEnd) +
    'window.commitFrameExpectation = value => rememberExpectedRender(value, true);})();', sandbox);
  function submit(scene, revision, traces) {
    const render = Object.assign({}, expected, {scene_id: scene, render_revision: revision,
      camera_revision: revision, camera_version: revision});
    const layout = {
      scene: {
        camera: {eye: {x: 1.8, y: 0, z: 0}, center: {x: 0, y: 0, z: 0},
          up: {x: 0, y: 0, z: 1}, projection: {type: 'perspective'}},
        uirevision: scene + '__name__' + revision,
      },
      meta: {mattervis_render: render},
    };
    return sandbox.window.Plotly.react(gd, {data: traces, layout: layout});
  }
  return {frames, gd, expected, nodes, root: sandbox.window, submit};
}
function witness(h) {
  assert.ok(h.nodes['mv-frame-witness'], 'witness element was not created');
  return JSON.parse(h.nodes['mv-frame-witness'].textContent);
}
"""


def _run(scenario: str) -> None:
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


def test_applied_frame_records_coordinate_count_and_identity():
    _run(r"""
  const h = harness();
  const typed = {type: 'mesh3d', name: 'bond', visible: true, x: {length: 4}};
  await h.submit('scene-a', 2, [typed]);
  const log = witness(h);
  assert.equal(log.length, 1);
  assert.equal(log[0].decision, 'applied');
  assert.equal(log[0].scene_id, 'scene-a');
  assert.equal(log[0].render_revision, 2);
  assert.equal(log[0].incoming.traces, 1);
  assert.equal(log[0].incoming.coordinates, 4);
  assert.equal(log[0].incoming.visible_coordinates, 4);
  assert.equal(log[0].camera.eye.x, 1.8);
  assert.equal(log[0].camera.projection, 'perspective');
  assert.equal(h.frames.length, 1);
  assert.equal(h.gd.__mvFrameWitness.length, 1);
""")


def test_stale_frame_is_recorded_and_does_not_paint():
    _run(r"""
  const h = harness();
  await h.submit('scene-a', 2, [{type: 'mesh3d', visible: true, x: [0, 1]}]);
  h.expected.render_revision = 4;
  h.expected.camera_revision = 4;
  h.root.commitFrameExpectation(JSON.parse(JSON.stringify(h.expected)));
  await h.submit('scene-a', 1, [{type: 'mesh3d', visible: true, x: [0]}]);
  const log = witness(h);
  assert.equal(h.frames.length, 1, 'stale frame reached Plotly');
  assert.equal(log.at(-1).decision, 'stale-before-queue');
  assert.equal(log.at(-1).render_revision, 1);
  assert.equal(log.at(-1).incoming.coordinates, 1);
  assert.equal(log[0].decision, 'applied');
  assert.equal(h.gd.data.length, 1, 'stale record replaced the mounted traces');
""")
