"""A delayed pick stays with the scene generation that received the click."""

import shutil
import subprocess
from pathlib import Path

import pytest


MATTERVIS_JS = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"


def test_selection_retry_invalidates_on_scene_generation_and_a_newer_pointer():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  function bindRightClick()');
assert.ok(stop > 0);
const plot = {
  layout: {meta: {mattervis_render: {scene_id: 'scene-a', scene_generation: 3}}},
  querySelector() { return null; },
};
const tabs = {
  querySelector(sel) {
    if (String(sel).indexOf('aria-selected') >= 0) return {id: 'scene-tab-' + selected};
    return null;
  },
  querySelectorAll() { return []; },
};
let selected = 'scene-a';
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise, Math,
  setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
  MutationObserver: function () { this.observe = function () {}; },
  window: {
    location: {protocol: 'http:', host: 'localhost'},
    Plotly: null,
    WebSocket: null,
    dash_clientside: {set_props() {}},
  },
  document: {
    getElementById(id) {
      if (id === 'crystal-graph') return {querySelector() { return plot; }};
      if (id === 'scene-tabs') return tabs;
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(0, stop) + '\n})();', sandbox);
const fence = sandbox.window.MatterVisSelectionFence;
assert.equal(typeof fence.current, 'function');
assert.equal(fence.sceneGeneration(), '3');
const first = fence.bump();
assert.equal(fence.current(first, 'scene-a', '3'), true);
plot.layout.meta.mattervis_render.scene_generation = 4;
assert.equal(fence.current(first, 'scene-a', '3'), false, 'a new scene generation cancels the click');
plot.layout.meta.mattervis_render.scene_generation = 3;
selected = 'scene-b';
assert.equal(fence.current(first, 'scene-a', '3'), false, 'another scene cancels the click');
selected = 'scene-a';
const second = fence.bump();
assert.equal(fence.current(first, 'scene-a', '3'), false, 'a newer pointer cancels the click');
assert.equal(fence.current(second, 'scene-a', '3'), true);
plot.__mvGraphGeneration = 9;
assert.equal(fence.current(second, 'scene-a', '3'), true, 'graph generation during settle does not cancel');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
