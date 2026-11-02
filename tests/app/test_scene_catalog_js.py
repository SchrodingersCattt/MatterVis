"""Scene-catalog messages reuse the open page's tab callback."""

import shutil
import subprocess
from pathlib import Path

import pytest


MATTERVIS_JS = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"


def test_scene_catalog_reconcile_keeps_selection_and_drops_stale_seq():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  // ── Right-click menu');
assert.ok(stop > 0);
const calls = [];
function tab(id, label) {
  return {
    id: 'scene-tab-' + id,
    querySelector(sel) {
      return sel === '.scene-tab-text' ? {textContent: label} : null;
    },
  };
}
const dom = {tabs: [tab('a', 'Alpha')], selected: 'a'};
const tabs = {
  querySelector(sel) {
    if (String(sel).indexOf('aria-selected') >= 0) return {id: 'scene-tab-' + dom.selected};
    return null;
  },
  querySelectorAll() { return dom.tabs; },
};
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise,
  setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
  MutationObserver: function () { this.observe = function () {}; },
  window: {
    location: {protocol: 'http:', host: 'localhost'},
    Plotly: null,
    WebSocket: null,
    dash_clientside: {
      set_props(id, props) { calls.push({id, data: props.data}); return true; },
    },
  },
  document: {
    getElementById(id) { return id === 'scene-tabs' ? tabs : null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(0, stop) + '\n})();', sandbox);
const reconcile = sandbox.window.MatterVisReconcileSceneCatalog;
assert.equal(typeof reconcile, 'function');

reconcile({scene_catalog_seq: 1, scenes: [{id: 'a', label: 'Alpha'}], active_id: 'a', focus: false});
assert.equal(calls.length, 0, 'matching tabs must not rebuild');
assert.equal(sandbox.window.__mvSceneCatalogApplied.seq, 1);

reconcile({scene_catalog_seq: 2, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Beta'}], active_id: 'b', focus: false});
assert.equal(calls.length, 1);
assert.equal(calls[0].id, 'scene-event-store');
assert.equal(calls[0].data.source, 'scene_catalog');
assert.equal(calls[0].data.focus, false);
assert.equal(calls[0].data.catalog_seq, 2);

reconcile({scene_catalog_seq: 1, scenes: [{id: 'a', label: 'Alpha'}], active_id: 'a', focus: true});
assert.equal(calls.length, 1, 'an older catalog must not move the tab');

dom.tabs = [tab('a', 'Alpha'), tab('b', 'Beta')];
reconcile({scene_catalog_seq: 3, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Beta'}], active_id: 'b', focus: true});
assert.equal(calls.length, 2);
assert.equal(calls[1].data.focus, true);
assert.equal(calls[1].data.active_id, 'b');

reconcile({scene_catalog_seq: 3, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Beta'}], active_id: 'a', focus: false});
assert.equal(calls.length, 2, 'a repeated catalog must not focus backward');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_http_fallback_and_snapshot_feed_the_same_reconciler():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    assert "if (body && body.scene_catalog) reconcileSceneCatalog(body.scene_catalog);" in source
    assert 'if (p.type === "scene_catalog")' in source
    assert "if (p.scene_catalog) reconcileSceneCatalog(p.scene_catalog);" in source
    assert "window.MatterVisReconcileSceneCatalog = reconcileSceneCatalog;" in source
