"""Execute the real redraw function through on/off/on, not source assertions."""

from pathlib import Path
import shutil
import subprocess

import pytest


def test_axes_off_clears_existing_svg_and_can_be_enabled_again():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to exercise the frontend function")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const begin = source.indexOf('  function redrawCompass(');
const end = source.indexOf('  /* rAF coalesce:', begin);
assert.ok(begin >= 0 && end > begin);
const svg = {children: []};
const sandbox = {
  window: {}, SVG_LAYER_ID: 'mv-compass-svg', dragPollActive: false, dragArm: null,
  compassFromMeta: layout => layout.meta?.compass || layout.meta?.compass_context || null,
  graphRoot: () => ({querySelector: () => svg}),
  clearSvg: target => {target.children = [];},
  cameraScreenBasis: () => ({}), projectLattice: () => [[1, 0]],
  ensureSvgLayer: () => svg,
  drawCompassSvg: target => {target.children = ['current-arrows'];},
  hasCompleteCamera: camera => !!(camera && camera.eye && camera.center && camera.up),
  displayedSceneCamera: () => null,
  layoutSceneCamera: () => ({eye:{x:0,y:0,z:1}, center:{x:0,y:0,z:0}, up:{x:0,y:1,z:0}}),
};
vm.createContext(sandbox);
vm.runInContext(source.slice(begin, end), sandbox);
const frame = {layout: {meta: {compass: {M: [[1,0,0],[0,1,0],[0,0,1]]}}}};
sandbox.redrawCompass(frame, {}, false);
assert.equal(svg.children.length, 1);
frame.layout.meta = {};
sandbox.redrawCompass(frame, {}, false);
assert.equal(svg.children.length, 0, 'old arrows survived Axes off');
frame.layout.meta = {compass: {M: [[1,0,0],[0,1,0],[0,0,1]]}};
sandbox.redrawCompass(frame, {}, false);
assert.equal(svg.children.length, 1, 'Axes on did not restore arrows');
sandbox.window.__mv_axes_enabled = false;
sandbox.redrawCompass(frame, {}, false);
assert.equal(svg.children.length, 0, 'disabled axes were redrawn from stale figure metadata');
sandbox.window.__mv_axes_enabled = true;
sandbox.redrawCompass(frame, {}, false);
assert.equal(svg.children.length, 1, 're-enabled axes did not restore arrows');

// An initial frame can carry only the saved context while Axes is off.
frame.layout.meta = {compass_context: {enabled: false, M: [[1,0,0],[0,1,0],[0,0,1]]}};
sandbox.window.__mv_axes_enabled = undefined;
sandbox.redrawCompass(frame, {}, false);
assert.equal(svg.children.length, 0, 'disabled context should stay hidden before a user toggle');
sandbox.window.__mv_axes_enabled = true;
sandbox.redrawCompass(frame, {}, false);
assert.equal(svg.children.length, 1, 'Axes could not be enabled from a stored context');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_compass_follows_displayed_camera_not_previous_layout():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to exercise the frontend function")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const begin = source.indexOf('  function redrawCompass(');
const end = source.indexOf('  /* rAF coalesce:', begin);
assert.ok(begin >= 0 && end > begin);
const seen = [];
const displayed = {eye:{x:2,y:0,z:0}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1}};
const layoutCamera = {eye:{x:0,y:0,z:8}, center:{x:0,y:0,z:0}, up:{x:0,y:1,z:0}};
const eventCamera = {eye:{x:0,y:9,z:0}, center:{x:0,y:0,z:0}, up:{x:1,y:0,z:0}};
const sandbox = {
  window: {}, SVG_LAYER_ID: 'mv-compass-svg', dragPollActive: false, dragArm: null,
  compassFromMeta: layout => layout.meta.compass,
  graphRoot: () => ({querySelector: () => ({})}),
  clearSvg: () => {},
  hasCompleteCamera: camera => !!(camera && camera.eye && camera.center && camera.up),
  displayedSceneCamera: () => displayed,
  layoutSceneCamera: () => layoutCamera,
  cameraScreenBasis: camera => { seen.push(camera); return {right:[1,0,0], screenUp:[0,1,0]}; },
  projectLattice: () => [[1, 0], [0, 1], [0, 0]],
  ensureSvgLayer: () => ({}),
  drawCompassSvg: () => {},
};
vm.createContext(sandbox);
vm.runInContext(source.slice(begin, end), sandbox);
const frame = {layout: {meta: {compass: {M: [[1,0,0],[0,1,0],[0,0,1]]}}}};
sandbox.redrawCompass(frame, eventCamera, false);
assert.equal(seen[0], displayed, 'display click redrew the compass from the previous layout camera');
sandbox.displayedSceneCamera = () => null;
sandbox.redrawCompass(frame, eventCamera, false);
assert.equal(seen[1], eventCamera);
sandbox.redrawCompass(frame, null, false);
assert.equal(seen[2], layoutCamera);
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_compass_anchors_to_canvas_and_separates_overlapping_labels():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to exercise the frontend function")
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const begin = source.indexOf('  function clearSvg(');
const end = source.indexOf('  function displayedSceneCamera(', begin);
assert.ok(begin >= 0 && end > begin);
function rect(left, top, width, height) {
  return {left, top, width, height, right: left+width, bottom: top+height};
}
const canvas = {getBoundingClientRect: () => rect(0, 40, 800, 400)};
const gd = {_fullLayout:{scene:{_scene:{glplot:{canvas}}}}};
function element() {
  const node = {attrs: {}, children: [], style: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(child) { this.children.push(child); },
    removeChild(child) { this.children = this.children.filter(item => item !== child); },
  };
  Object.defineProperty(node, 'firstChild', {get() { return this.children[0] || null; }});
  return node;
}
const svg = element();
const sandbox = {
  document: {createElementNS: () => element()},
  graphRoot: () => ({getBoundingClientRect: () => rect(0, 0, 800, 600)}),
  graphDiv: () => Object.assign(gd, {getBoundingClientRect: () => rect(0, 0, 800, 600)}),
};
vm.createContext(sandbox);
vm.runInContext(source.slice(begin, end), sandbox);
const ctx = {anchor:[0.10, 0.18], pixel_length: 70, label_pixel_offset: 8, font_size: 16, labels:['a','b','c']};
sandbox.drawCompassSvg(svg, ctx, [[1, 0.02], [0.98, 0.0], [0, 1]]);
const texts = svg.children.filter(child => child.textContent);
assert.equal(texts.length, 3);
const ax = texts.map(node => Number(node.attrs.x));
const ay = texts.map(node => Number(node.attrs.y));
for (let i = 0; i < texts.length; i++) {
  for (let j = i + 1; j < texts.length; j++) {
    const dist = Math.hypot(ax[i] - ax[j], ay[i] - ay[j]);
    assert.ok(dist >= 16, 'labels overlap: ' + dist);
  }
}
const line = svg.children.find(child => child.attrs.y1 !== undefined);
const anchorY = Number(line.attrs.y1);
const graphAnchorY = (1 - 0.18) * 600;
const canvasAnchorY = 40 + (1 - 0.18) * 400;
assert.ok(Math.abs(anchorY - canvasAnchorY) < 0.5, 'triad is anchored to the graph div, not the canvas');
assert.ok(Math.abs(anchorY - graphAnchorY) > 20);
const heads = svg.children.filter(child => child.attrs.points);
assert.equal(heads.length, 3);
sandbox.drawCompassSvg(svg, ctx, [[0.08, 0], [1, 0], [0, 1]]);
const shortHeads = svg.children.filter(child => child.attrs.points);
assert.ok(shortHeads.length < 3, 'a short shaft still grew an arrowhead past the anchor');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr
