"""A scene switch must frame the new figure without waiting for a resize."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_scene_switch_adopts_the_incoming_axis_box_and_camera():
    """The new geometry stays in the previous gl3d box until a scene relayout.

    ``Plotly.react`` stores the incoming ranges and camera on the layout.
    The WebGL scene keeps drawing with the previous box until a later
    relayout rebuilds it, which is what a plot resize does. The switch
    itself has to issue that relayout. A later frame for the same scene
    must not replace a live drag, and the follow-up must not be reported
    as a pointer relayout.
    """

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const shown = {
  range: [-30, 30],
  projection: 'perspective',
  eye: 1.6,
};
const events = [];
const boxRelayouts = [];
let expected = {scene_id: 'scene-a', scene_generation: 1, render_revision: 1,
  camera_revision: 0, server_started_at: 'epoch'};
function figure(sceneId, span, projection) {
  return {
    data: [{type: 'scatter3d', mode: 'lines', x: [0, span], y: [0, 0], z: [0, 0]}],
    layout: {
      scene: {
        xaxis: {range: [-span, span]},
        yaxis: {range: [-span / 2, span / 2]},
        zaxis: {range: [-span / 2, span / 2]},
        aspectmode: 'manual',
        aspectratio: {x: 1, y: 0.5, z: 0.5},
        camera: {
          eye: {x: projection === 'orthographic' ? 0.4 : 1.6, y: 1.2, z: 0.2},
          center: {x: 0, y: 0, z: 0},
          up: {x: 0, y: 0, z: 1},
          projection: {type: projection},
        },
        uirevision: sceneId + '__name__0__box',
      },
      meta: {mattervis_render: {
        scene_id: sceneId,
        scene_generation: 1,
        render_revision: 1,
        camera_revision: 0,
        server_started_at: 'epoch',
      }},
    },
  };
}
const first = figure('scene-a', 30, 'perspective');
const gd = {
  data: first.data,
  layout: JSON.parse(JSON.stringify(first.layout)),
  emit(name) { events.push(name); },
};
function react(target, payload) {
  const figure = Array.isArray(payload) ? {data: payload, layout: arguments[2]} : payload;
  target.layout = JSON.parse(JSON.stringify(figure.layout));
  target.data = figure.data || [];
  return Promise.resolve(target);
}
function relayout(target, patch) {
  if (target.emit) target.emit('plotly_relayout');
  const adoptsBox = patch['scene.xaxis.range'] || patch['scene.camera'];
  if (adoptsBox) {
    boxRelayouts.push(JSON.parse(JSON.stringify(patch)));
    shown.range = patch['scene.xaxis.range'][0];
    shown.projection = patch['scene.camera'].projection.type;
    shown.eye = patch['scene.camera'].eye.x;
  }
  if (patch.width) {
    shown.range = target.layout.scene.xaxis.range[0];
    shown.projection = target.layout.scene.camera.projection.type;
    shown.eye = target.layout.scene.camera.eye.x;
  }
  return Promise.resolve(target);
}
const sandbox = {console, Promise, Object, Array, JSON, Number, String, Date,
  setTimeout, clearTimeout,
  window: {Plotly: {react, newPlot: react, relayout}},
  document: {getElementById(id) {
    if (id === 'crystal-graph') return {querySelector: () => gd};
    if (id === 'fast-view-metadata') return {get textContent() { return JSON.stringify(expected); }};
    return null;
  }}};
vm.createContext(sandbox);
const end = source.indexOf('  // ── Right-click menu');
vm.runInContext(source.slice(0, end) + '})();', sandbox);
(async function () {
expected = {scene_id: 'scene-b', scene_generation: 1, render_revision: 1,
  camera_revision: 0, server_started_at: 'epoch'};
const next = figure('scene-b', 1.2, 'orthographic');
await sandbox.window.Plotly.react(gd, next);
assert.equal(boxRelayouts.length, 1, 'scene switch left the previous gl3d box in place');
assert.deepEqual(boxRelayouts[0]['scene.xaxis.range'], [-1.2, 1.2]);
assert.deepEqual(boxRelayouts[0]['scene.yaxis.range'], [-0.6, 0.6]);
assert.equal(boxRelayouts[0]['scene.camera'].projection.type, 'orthographic');
assert.equal(boxRelayouts[0]['scene.uirevision'], 'scene-b__name__0__box');
assert.equal(shown.range, -1.2, 'visible axis box is still the previous scene');
assert.equal(shown.projection, 'orthographic', 'visible camera kept the previous projection');
assert.equal(shown.eye, 0.4);
assert.deepEqual(events, [], 'scene-box relayout was reported as a pointer gesture');
const same = figure('scene-b', 1.2, 'orthographic');
same.layout.scene.camera.eye.x = 9;
await sandbox.window.Plotly.react(gd, same);
assert.equal(boxRelayouts.length, 1, 'same scene replaced the live camera with another box relayout');
assert.equal(shown.eye, 0.4, 'same-scene frame moved the gl camera');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
