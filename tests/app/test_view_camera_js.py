"""Regression coverage for serialized client-side camera relayouts."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_camera_projection_and_reset_are_applied_in_order():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const gd = {};
const calls = [];
const pending = [];
const sandbox = {
  Promise, Object, Array, JSON,
  window: {
    Plotly: {
      relayout: (_gd, patch) => {
        calls.push(patch['scene.camera']);
        return new Promise((resolve, reject) => pending.push({resolve, reject}));
      },
    },
  },
  document: {getElementById: id => id === 'crystal-graph' ? {querySelector: () => gd} : null},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const apply = sandbox.window.MatterVisViewCamera.apply;
const camera = type => ({
  eye: {x: 0, y: 0, z: 1.8},
  center: {x: 0, y: 0, z: 0},
  up: {x: 0, y: 1, z: 0},
  projection: {type},
});
(async () => {
  const waitForCall = async expected => {
    for (let i = 0; i < 8 && calls.length < expected; i++) await Promise.resolve();
    assert.equal(calls.length, expected);
  };
  assert.equal(apply(camera('orthographic')), true);
  assert.equal(apply(camera('perspective')), true);
  assert.equal(apply(camera('orthographic')), true); // reset after the switch
  await waitForCall(1);
  pending.shift().resolve();
  await waitForCall(2);
  pending.shift().reject(new Error('projection relayout failed'));
  await waitForCall(3);
  assert.equal(calls[2].projection.type, 'orthographic', 'reset camera was reordered');
  pending.shift().resolve();
  await gd.__mvRenderQueue;
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
