"""One catalog read serves scene names and commit focus.

The workbench stack keeps the Dash label adapter, the selection dock, the
typed commit focus, the selection fence, and the frame witness as separate
changes. This check loads the composed browser script and proves the shared
catalog path still does both jobs, and that a render queue already installed
by the camera module is left in place.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parents[2] / "frontend" / "assets"
MATTERVIS_JS = ASSETS / "mattervis.js"
VIEW_CAMERA_JS = ASSETS / "view_camera.js"


def test_shared_render_queue_stays_owned_by_the_camera_module():
    mattervis = MATTERVIS_JS.read_text(encoding="utf-8")
    camera = VIEW_CAMERA_JS.read_text(encoding="utf-8")
    assert "window.MatterVisRenderQueue = window.MatterVisRenderQueue ||" in mattervis
    assert "MatterVisRenderQueue" in camera
    assert "MatterVisCamera =" not in mattervis


def test_one_catalog_read_names_the_scene_and_focuses_the_commit():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  // ── Right-click menu');
assert.ok(stop > 0, 'catalog reconcile must stay above the selection menu');
const queue = {owner: 'camera'};
const status = {textContent: 'Committed build_supercell in scene scene_result00001.'};
const tabs = [
  {
    id: 'scene-tab-scene_source00001',
    getAttribute(name) {
      if (name === 'data-display-name') return 'Stale attribute';
      if (name === 'data-scene-id') return 'scene_source00001';
      return null;
    },
    querySelector() { return null; },
  },
  {
    id: 'scene-tab-scene_result00001',
    getAttribute(name) {
      if (name === 'data-display-name') return 'Stale attribute';
      if (name === 'data-scene-id') return 'scene_result00001';
      return null;
    },
    querySelector() { return null; },
  },
];
const root = {
  querySelector(sel) {
    if (String(sel).indexOf('aria-selected') >= 0) return tabs[0];
    return null;
  },
  querySelectorAll() { return tabs; },
};
const selected = [];
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise,
  setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
  MutationObserver: function () { this.observe = function () {}; },
  window: {
    location: {protocol: 'http:', host: 'localhost'},
    Plotly: null,
    WebSocket: null,
    MatterVisRenderQueue: queue,
    dash_clientside: {set_props() {}},
  },
  document: {
    getElementById(id) {
      if (id === 'scene-tabs') return root;
      if (id === 'upload-status') return status;
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(0, stop) + '\n})();', sandbox);
assert.equal(sandbox.window.MatterVisRenderQueue, queue, 'workbench replaced the shared render queue');
const note = sandbox.window.MatterVisNoteCommitFocus;
const reconcile = sandbox.window.MatterVisReconcileSceneCatalog;
const payload = {
  operation: 'build_supercell',
  source_scene_id: 'scene_source00001',
  result_scene_id: 'scene_result00001',
  revision: 2,
  generation: 4,
};
const original = sandbox.window.MatterVisSelectScene;
sandbox.window.MatterVisSelectScene = function (id) {
  selected.push(id);
  original(id);
};
assert.equal(note(status.textContent), null);
assert.deepEqual(selected, []);
assert.equal(note(payload), null);
assert.deepEqual(selected, []);
reconcile({
  scene_catalog_seq: 1,
  scenes: [{id: 'scene_source00001', label: 'Source'}],
  active_id: 'scene_source00001',
  focus: false,
});
assert.deepEqual(selected, []);
assert.equal(status.textContent, 'Loaded: Source');
assert.equal(sandbox.window.MatterVisSceneLabels.catalogLabel('scene_source00001'), 'Source');
reconcile({
  scene_catalog_seq: 2,
  scenes: [
    {id: 'scene_source00001', label: 'Source'},
    {id: 'scene_result00001', label: 'Committed cell'},
  ],
  active_id: 'scene_source00001',
  focus: false,
});
assert.deepEqual(selected, ['scene_result00001']);
assert.equal(status.textContent, 'Loaded: Committed cell');
assert.equal(sandbox.window.MatterVisSceneLabels.labelFor('scene_result00001', tabs[1]), 'Committed cell');
const before = selected.length;
reconcile({
  scene_catalog_seq: 2,
  scenes: [
    {id: 'scene_source00001', label: 'Source'},
    {id: 'scene_result00001', label: 'Committed cell'},
  ],
  active_id: 'scene_source00001',
  focus: false,
});
assert.equal(selected.length, before, 'replaying the catalog selected the result again');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
