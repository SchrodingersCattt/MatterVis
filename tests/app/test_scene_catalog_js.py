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
      set_props(id, props) { calls.push({id, data: props.data, props}); return true; },
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
dom.tabs = [];
reconcile({scene_catalog_seq: 2, scenes: [{id: 'gone', label: 'Gone'}], active_id: 'gone', focus: false});
assert.equal(calls.length, 1, 'a catalog sequence is applied once');

dom.tabs = [tab('a', 'Alpha'), tab('b', 'Beta')];
reconcile({scene_catalog_seq: 3, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Beta'}], active_id: 'b', focus: true});
assert.equal(calls.length, 2);
assert.equal(calls[1].data.focus, true);
assert.equal(calls[1].data.active_id, 'b');

reconcile({scene_catalog_seq: 3, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Beta'}], active_id: 'a', focus: false});
assert.equal(calls.length, 2, 'a repeated catalog must not focus backward');
assert.equal(calls.filter((call) => call.id === 'scene-tabs').length, 0, 'focus:false must not move another page');

dom.tabs = [tab('a', 'Alpha')];
dom.selected = 'a';
sandbox.window.__mvOwnSceneFocus = true;
reconcile({scene_catalog_seq: 4, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Beta'}], active_id: 'b', focus: false});
const tabCall = calls.find((call) => call.id === 'scene-tabs');
assert.ok(tabCall, 'the duplicating page selects the new scene');
assert.equal(tabCall.props.value, 'b');
assert.equal(sandbox.window.__mvOwnSceneFocus, false);
const catalogCall = calls.find((call) => call.id === 'scene-event-store' && call.data.catalog_seq === 4);
assert.equal(catalogCall.data.focus, false);
assert.equal(catalogCall.data.active_id, 'b');

reconcile({scene_catalog_seq: 5, scenes: [{id: 'a', label: 'Alpha'}, {id: 'c', label: 'Gamma'}], active_id: 'c', focus: false});
assert.equal(calls.filter((call) => call.id === 'scene-tabs').length, 1, 'a later catalog does not move a page that did not click Duplicate');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_string_tab_label_rename_still_reconciles():
    """Tab text is the label only when data-display-name is absent."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  // ── Right-click menu');
const calls = [];
const dom = {tabs: [{id: 'scene-tab-a', textContent: 'Alpha', querySelector() { return null; }}]};
const tabs = {
  querySelector(sel) {
    if (String(sel).indexOf('aria-selected') >= 0) return {id: 'scene-tab-a'};
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
    Plotly: null, WebSocket: null,
    dash_clientside: {set_props(id, props) { calls.push({id, data: props.data}); return true; }},
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
reconcile({scene_catalog_seq: 1, scenes: [{id: 'a', label: 'Alpha'}], active_id: 'a', focus: false});
assert.equal(calls.length, 0);
reconcile({scene_catalog_seq: 2, scenes: [{id: 'a', label: 'Renamed'}], active_id: 'a', focus: false});
assert.equal(calls.length, 1);
assert.equal(calls[0].data.source, 'scene_catalog');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_catalog_attribute_beats_stale_tab_text():
    """data-display-name is compared before tab text, including a stale label."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  // ── Right-click menu');
const calls = [];
function attrs(values) {
  return {
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(values, name) ? values[name] : null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(values, name); },
  };
}
const dom = {tabs: [], carriers: []};
function tabWithOwnName(text, displayName) {
  return Object.assign({
    id: 'scene-tab-a',
    textContent: text,
    querySelector() { return null; },
  }, attrs({'data-scene-id': 'a', 'data-display-name': displayName}));
}
function staleTextTab(text) {
  return {
    id: 'scene-tab-a',
    textContent: text,
    querySelector(sel) {
      if (sel === '.scene-tab-text') return {textContent: text};
      return null;
    },
  };
}
const tabs = {
  querySelector(sel) {
    if (String(sel).indexOf('aria-selected') >= 0) return {id: 'scene-tab-a'};
    return null;
  },
  querySelectorAll() { return dom.tabs; },
};
const status = {textContent: '', style: {}, className: ''};
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise,
  setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
  MutationObserver: function () { this.observe = function () {}; },
  window: {
    location: {protocol: 'http:', host: 'localhost'},
    Plotly: null, WebSocket: null,
    dash_clientside: {set_props(id, props) { calls.push({id, data: props.data}); return true; }},
  },
  document: {
    getElementById(id) {
      if (id === 'scene-tabs') return tabs;
      if (id === 'upload-status') return status;
      return null;
    },
    querySelectorAll(sel) { return sel === '[data-scene-id]' ? dom.carriers : []; },
    addEventListener() {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(0, stop) + '\nwindow.__mvSceneTabTest = {noteLoadedScene};\n})();', sandbox);
const reconcile = sandbox.window.MatterVisReconcileSceneCatalog;
const noteLoadedScene = sandbox.window.__mvSceneTabTest.noteLoadedScene;

dom.tabs = [tabWithOwnName('Stale ×', 'Alpha')];
reconcile({scene_catalog_seq: 1, scenes: [{id: 'a', label: 'Alpha'}], active_id: 'a', focus: false});
assert.equal(calls.length, 0, 'catalog label matches data-display-name, not the stale text');
reconcile({scene_catalog_seq: 2, scenes: [{id: 'a', label: 'Stale ×'}], active_id: 'a', focus: false});
assert.equal(calls.length, 1, 'tab text must not satisfy a catalog label the attribute rejects');

calls.length = 0;
dom.tabs = [staleTextTab('Stale')];
dom.carriers = [Object.assign({id: 'scene-tab-close-row-a'}, attrs({'data-scene-id': 'a', 'data-display-name': 'Alpha'}))];
reconcile({scene_catalog_seq: 3, scenes: [{id: 'a', label: 'Alpha'}], active_id: 'a', focus: false});
assert.equal(calls.length, 0, 'a carrier attribute wins over stale .scene-tab-text');
reconcile({scene_catalog_seq: 4, scenes: [{id: 'a', label: 'Stale'}], active_id: 'a', focus: false});
assert.equal(calls.length, 1);

status.textContent = '';
noteLoadedScene('a', dom.tabs[0]);
assert.equal(status.textContent, 'Loaded: Stale');
assert.equal(sandbox.window.MatterVisSceneLabels.catalogLabel('a'), 'Stale');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_catalog_rename_updates_the_loaded_scene_and_ignores_a_replay():
    """The catalog message renames the open scene. A repeated sequence cannot."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const stop = source.indexOf('  // ── Right-click menu');
const calls = [];
function attrs(values) {
  return {
    getAttribute(name) { return Object.prototype.hasOwnProperty.call(values, name) ? values[name] : null; },
    hasAttribute(name) { return Object.prototype.hasOwnProperty.call(values, name); },
  };
}
const dom = {tabs: [], selected: 'a'};
function tab(id, displayName) {
  return Object.assign({
    id: 'scene-tab-' + id,
    textContent: 'stale ' + id,
    querySelector() { return null; },
  }, attrs({'data-scene-id': id, 'data-display-name': displayName}));
}
const tabs = {
  querySelector(sel) {
    if (String(sel).indexOf('aria-selected') >= 0) return {id: 'scene-tab-' + dom.selected};
    return null;
  },
  querySelectorAll() { return dom.tabs; },
};
const status = {textContent: '', style: {}, className: ''};
const sandbox = {
  console, Date, JSON, Number, String, Object, Array, Promise,
  setTimeout() {}, clearTimeout() {}, requestAnimationFrame() {},
  MutationObserver: function () { this.observe = function () {}; },
  window: {
    location: {protocol: 'http:', host: 'localhost'},
    Plotly: null, WebSocket: null,
    dash_clientside: {set_props(id, props) { calls.push({id, data: props.data}); return true; }},
  },
  document: {
    getElementById(id) {
      if (id === 'scene-tabs') return tabs;
      if (id === 'upload-status') return status;
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(0, stop) + '\n})();', sandbox);
const reconcile = sandbox.window.MatterVisReconcileSceneCatalog;
const labels = sandbox.window.MatterVisSceneLabels;
dom.tabs = [tab('a', 'Alpha'), tab('b', 'Beta')];
reconcile({scene_catalog_seq: 1, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Beta'}], active_id: 'a', focus: false});
assert.equal(status.textContent, 'Loaded: Alpha');
assert.equal(calls.length, 0);
reconcile({scene_catalog_seq: 2, scenes: [{id: 'a', label: 'Alpha'}, {id: 'b', label: 'Gamma'}], active_id: 'a', focus: false});
assert.equal(status.textContent, 'Loaded: Alpha', 'an inactive rename must not replace the loaded line');
assert.equal(labels.catalogLabel('b'), 'Gamma');
assert.equal(labels.catalogLabel('a'), 'Alpha');
assert.equal(calls.length, 1);
assert.equal(calls[0].data.focus, false);
reconcile({scene_catalog_seq: 2, scenes: [{id: 'a', label: 'Hijack'}, {id: 'b', label: 'Other'}], active_id: 'b', focus: false});
assert.equal(labels.catalogLabel('a'), 'Alpha');
assert.equal(labels.catalogLabel('b'), 'Gamma');
assert.equal(status.textContent, 'Loaded: Alpha');
assert.equal(calls.length, 1, 'a repeated catalog sequence must not move the tab');
dom.tabs = [tab('a', 'Alpha'), tab('b', 'Gamma')];
reconcile({scene_catalog_seq: 3, scenes: [{id: 'a', label: 'Renamed'}, {id: 'b', label: 'Gamma'}], active_id: 'a', focus: false});
assert.equal(status.textContent, 'Loaded: Renamed');
assert.equal(labels.labelFor('a', dom.tabs[0]), 'Renamed');
assert.notEqual(status.textContent, 'Loaded: stale a');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_catalog_rename_updates_loaded_line_without_tab_text():
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
const status = {textContent: ''};
function tab(id, label, displayName) {
  return {
    id: 'scene-tab-' + id,
    getAttribute(name) {
      return name === 'data-display-name' ? displayName : null;
    },
    querySelector(sel) {
      if (sel === '.scene-tab-text') return {textContent: label, getAttribute() { return ''; }};
      return null;
    },
  };
}
const dom = {tabs: [tab('a', 'Stale tab text', 'Stale attribute')], selected: 'a'};
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
    dash_clientside: {set_props() { return true; }},
  },
  document: {
    getElementById(id) {
      if (id === 'scene-tabs') return tabs;
      if (id === 'upload-status') return status;
      return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(0, stop) + '\n})();', sandbox);
const reconcile = sandbox.window.MatterVisReconcileSceneCatalog;
reconcile({
  scene_catalog_seq: 1,
  scenes: [{id: 'a', label: 'Renamed from catalog'}],
  active_id: 'a',
  focus: false,
});
assert.equal(status.textContent, 'Loaded: Renamed from catalog');
assert.equal(dom.tabs[0].querySelector('.scene-tab-text').textContent, 'Stale tab text');
assert.equal(dom.tabs[0].getAttribute('data-display-name'), 'Stale attribute');
reconcile({
  scene_catalog_seq: 2,
  scenes: [{id: 'a', label: 'Renamed again'}],
  active_id: 'a',
  focus: false,
});
assert.equal(status.textContent, 'Loaded: Renamed again');
assert.equal(dom.tabs[0].querySelector('.scene-tab-text').textContent, 'Stale tab text');
"""
    result = subprocess.run(
        [node, "-e", program, str(MATTERVIS_JS)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_http_fallback_and_snapshot_feed_the_same_reconciler():
    source = MATTERVIS_JS.read_text(encoding="utf-8")
    assert "if (body && body.scene_catalog) reconcileSceneCatalog(body.scene_catalog);" in source
    assert 'if (p.type === "scene_catalog")' in source
    assert "if (p.scene_catalog) reconcileSceneCatalog(p.scene_catalog);" in source
    assert "window.MatterVisReconcileSceneCatalog = reconcileSceneCatalog;" in source
    assert 'closest("#scene-new-tab-btn")' in source
    assert "window.__mvOwnSceneFocus = true;" in source
