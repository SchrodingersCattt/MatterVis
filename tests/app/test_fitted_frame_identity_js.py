"""Fitted frames stay bound to the scene generation and server epoch.

The range guard records that fit restore belongs to the frame task. It does
not replace ``Plotly.react``.
"""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_fitted_identity_tracks_generation_and_server_epoch():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_camera.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const react = () => Promise.resolve();
const sandbox = {
  Promise, Object, Array, JSON, Number, String, Date, setTimeout, clearTimeout,
  window: {Plotly: {relayout: () => Promise.resolve(), react}},
  document: {getElementById: () => null, addEventListener: () => {}},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
assert.equal(sandbox.window.Plotly.react, react, 'loading the camera module wrapped Plotly.react');
const matches = sandbox.window.MatterVisViewCamera.fittedIdentityMatches;
assert.equal(matches(
  {generation: 1, epoch: 'epoch-a'},
  {scene_generation: 4, server_started_at: 'epoch-a'},
), false, 'a new scene generation inherited the previous fit');
assert.equal(matches(
  {generation: 1, epoch: 'epoch-a'},
  {scene_generation: 1, server_started_at: 'epoch-b'},
), false, 'a restarted server inherited the previous fit');
assert.equal(matches(
  {generation: 1, epoch: 'epoch-a'},
  {scene_generation: 1, server_started_at: 'epoch-a'},
), true, 'the same generation and server epoch rejected its fit');
assert.equal(matches(
  {generation: '', epoch: ''},
  {scene_generation: '', server_started_at: ''},
), true, 'an unscoped fit rejected an unscoped frame');
"""
    result = subprocess.run(
        [node, "-e", program, str(source)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
