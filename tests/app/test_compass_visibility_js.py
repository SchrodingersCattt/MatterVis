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
  drawCompassSvg: target => {target.children = ['current-arrows'];}
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
