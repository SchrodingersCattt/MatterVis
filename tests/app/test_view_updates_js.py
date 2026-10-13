"""Regression coverage for the browser-side view update handoff."""

from pathlib import Path
import shutil
import subprocess

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
    Plotly: {react: (_gd, data, layout) => {calls.push({data, layout}); return Promise.resolve();}},
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
        text=True,
        timeout=15,
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
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
