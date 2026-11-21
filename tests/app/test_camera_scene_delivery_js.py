"""One frame task owns projection, fit restore, and the acknowledged camera.

The cases are synthetic graphs. They do not load a structure file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_frame_task_keeps_projection_fit_and_ack_on_one_boundary():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const root = process.argv[1];
const mattervis = fs.readFileSync(root + '/frontend/assets/mattervis.js', 'utf8');
const viewCamera = fs.readFileSync(root + '/frontend/assets/view_camera.js', 'utf8');
const relayouts = [];
const reacts = [];
let selected = 'scene-a';
let active = null;
function layout(sceneId, generation, span, projection, revision) {
  const camera = {
    eye: {x: 1.25, y: 1.25, z: 1.25},
    center: {x: 0, y: 0, z: 0},
    up: {x: 0, y: 0, z: 1},
  };
  if (projection) camera.projection = {type: projection};
  return {
    data: [{type: 'scatter3d', x: [0, span], y: [0, 0], z: [0, 0]}],
    layout: {
      scene: {
        xaxis: {range: [-span, span]},
        yaxis: {range: [-span, span]},
        zaxis: {range: [-span, span]},
        camera: camera,
        uirevision: sceneId + '-box',
      },
      meta: {mattervis_render: {
        scene_id: sceneId,
        scene_generation: generation,
        render_revision: revision,
        camera_revision: 2,
        server_started_at: 'epoch-a',
      }},
    },
  };
}
function graph(span, ortho) {
  const first = layout('scene-a', 1, span, ortho ? 'orthographic' : 'perspective', 1);
  const gd = {
    data: first.data,
    layout: JSON.parse(JSON.stringify(first.layout)),
    _fullLayout: {scene: {
      xaxis: {range: [-span, span]},
      yaxis: {range: [-span, span]},
      zaxis: {range: [-span, span]},
      _scene: {
        glplot: {camera: {_ortho: ortho}, objects: [{}], canvas: {}},
        traces: {atom: {}},
        camera: {_ortho: ortho},
      },
    }},
  };
  return gd;
}
function react(target, payload) {
  const figure = Array.isArray(payload) ? {data: payload, layout: arguments[2]} : payload;
  const camera = figure.layout.scene.camera || {};
  reacts.push(camera.projection && camera.projection.type || null);
  target.layout = JSON.parse(JSON.stringify(figure.layout));
  target.data = figure.data || [];
  return Promise.resolve(target);
}
function relayout(target, patch) {
  relayouts.push(JSON.parse(JSON.stringify(patch)));
  return Promise.resolve(target);
}
const sandbox = {
  console, Promise, Object, Array, JSON, Number, String, Date, Math, setTimeout, clearTimeout,
  window: {
    Plotly: {react, newPlot: react, relayout, version: '2.0.0'},
    mattervisCurrentCamera(gd) {
      const camera = gd.layout && gd.layout.scene && gd.layout.scene.camera;
      return camera ? JSON.parse(JSON.stringify(camera)) : null;
    },
  },
  document: {getElementById(id) {
    if (id === 'fast-view-metadata') {
      return {get textContent() { return JSON.stringify({scene_id: selected, server_started_at: 'epoch-a'}); }};
    }
    if (id === 'crystal-graph') return {querySelector() { return active; }};
    return null;
  }},
};
vm.createContext(sandbox);
const end = mattervis.indexOf('  // ── Right-click menu');
vm.runInContext(mattervis.slice(0, end) + '\n})();', sandbox);
vm.runInContext(viewCamera, sandbox);
const plot = sandbox.window.Plotly;
const cameraApi = sandbox.window.MatterVisViewCamera;

(async () => {
  const foreign = graph(2, false);
  selected = 'scene-b';
  await plot.react(foreign, layout('scene-b', 1, 8, 'orthographic', 2));
  assert.equal(reacts.at(-1), 'orthographic', 'a foreign Plotly version still split on private _ortho');
  assert.equal(relayouts.length, 0, 'a foreign Plotly version issued a projection follow-up');
  assert.ok(
    (sandbox.window.__mvRendererDiag || []).some((row) => row.kind === 'plotly-private-skipped'),
    'the skipped private read was not recorded',
  );

  plot.version = undefined;
  const fitted = cameraApi.fittedIdentityMatches(
    {generation: 1, epoch: 'epoch-a', ranges: {x: [-1, 1], y: [-1, 1], z: [-1, 1]}},
    {scene_generation: 4, server_started_at: 'epoch-a'},
  );
  assert.equal(fitted, false, 'a new scene generation inherited the previous fit');
  const restarted = cameraApi.fittedIdentityMatches(
    {generation: 1, epoch: 'epoch-a'},
    {scene_generation: 1, server_started_at: 'epoch-b'},
  );
  assert.equal(restarted, false, 'a restarted server inherited the previous fit');
  assert.equal(cameraApi.fittedIdentityMatches(
    {generation: 1, epoch: 'epoch-a'},
    {scene_generation: 1, server_started_at: 'epoch-a'},
  ), true);

  sandbox.window.__mvServerCameraByScene = {
    'scene-a': {
      camera: {
        eye: {x: 0.2, y: 0.2, z: 0.2},
        center: {x: 0, y: 0, z: 0},
        up: {x: 0, y: 0, z: 1},
        projection: {type: 'perspective'},
      },
      camera_revision: 2,
      scene_generation: 1,
      server_started_at: 'epoch-a',
    },
  };
  relayouts.length = 0;
  reacts.length = 0;
  selected = 'scene-b';
  const sceneB = graph(2, false);
  active = sceneB;
  await plot.react(sceneB, layout('scene-b', 1, 3, 'perspective', 3));
  assert.equal(
    relayouts.some((patch) => patch['scene.camera.eye'] && patch['scene.camera.eye'].x === 0.2),
    false,
    'scene A acknowledged camera was written onto scene B',
  );

  selected = 'scene-a';
  await plot.react(sceneB, layout('scene-a', 1, 3, 'perspective', 4));
  assert.equal(
    relayouts.some((patch) => patch['scene.camera.eye'] && patch['scene.camera.eye'].x === 0.2),
    true,
    'returning to scene A did not paint its acknowledged camera',
  );
  assert.ok(String(sceneB.__mvDeliveredFrame || '').indexOf('scene-a') >= 0);

  const lost = graph(2, true);
  sandbox.window.MatterVisViewCamera.reapplyFittedFrame = function () {
    return Promise.reject(new Error('fit was not restored'));
  };
  selected = 'scene-c';
  await assert.rejects(plot.react(lost, layout('scene-c', 2, 5, 'perspective', 5)), /fit was not restored/);
  assert.equal(lost.__mvDeliveredFrame, undefined, 'a failed fit restore confirmed the frame');
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(ROOT)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
