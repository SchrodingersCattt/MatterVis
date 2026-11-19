"""The Fit shutter separates a camera republish from a geometry change."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parent / "fit_shutter.js"

# The Camera WebGL failure on 5a57b25. Only renderRevision differs.
_REPUBLISH = {
    "before": {
        "revision": 3,
        "renderRevision": 1,
        "sceneId": "scene_a3f49b72bc08",
        "projection": "orthographic",
        "eye": [1.05986632, 1.05986632, 0.99795881],
        "center": [0.02063584, 0.02063584, -0.04127167],
        "aspect": {"x": 2.52330847, "y": 1.96771761, "z": 1.04173285},
        "ranges": {"x": [3.4842, 4.8358], "y": [3.673, 4.727], "z": [3.721, 4.279]},
        "status": "",
        "boundaryChecked": False,
        "plot": {"x": 341, "y": 50, "width": 939, "height": 682},
    },
    "after": {
        "revision": 3,
        "renderRevision": 2,
        "sceneId": "scene_a3f49b72bc08",
        "projection": "orthographic",
        "eye": [1.05986632, 1.05986632, 0.99795881],
        "center": [0.02063584, 0.02063584, -0.04127167],
        "aspect": {"x": 2.52330847, "y": 1.96771761, "z": 1.04173285},
        "ranges": {"x": [3.4842, 4.8358], "y": [3.673, 4.727], "z": [3.721, 4.279]},
        "status": "",
        "boundaryChecked": False,
        "plot": {"x": 341, "y": 50, "width": 939, "height": 682},
    },
}


def test_render_revision_tick_is_not_a_geometry_proof() -> None:
    """Same camera and box can hide a different mesh. The digest has to move."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the shutter predicate")
    program = r"""
const assert = require('node:assert/strict');
const shutter = require(process.argv[1]);
const recorded = JSON.parse(process.argv[2]);
const before = recorded.before;
const after = recorded.after;
assert.equal(shutter.sameCamera(before, after), true, 'the recorded pair moved the camera');
assert.equal(shutter.sameGeometry(before, after), false, 'a missing digest was treated as the same mesh');
assert.equal(shutter.sameStableFrame(before, after), false);
const identity = {
  sceneGeneration: '1',
  geometryVersion: 4,
  coordinateDigest: shutter.coordinateDigest([
    {type: 'scatter3d', x: [0, 1], y: [0, 1], z: [0, 1]},
  ]),
};
const stableBefore = Object.assign({}, before, identity);
const stableAfter = Object.assign({}, after, identity);
assert.equal(shutter.sameStableFrame(stableBefore, stableAfter), true,
  'a render-revision tick of the same coordinates was rejected');
const moved = Object.assign({}, stableAfter, {
  coordinateDigest: shutter.coordinateDigest([
    {type: 'scatter3d', x: [0, 2], y: [0, 1], z: [0, 1]},
  ]),
});
assert.equal(shutter.sameCamera(stableBefore, moved), true, 'the counterexample changed the camera');
assert.equal(shutter.sameGeometry(stableBefore, moved), false,
  'the same camera and data box hid a different mesh');
assert.equal(shutter.sameStableFrame(stableBefore, moved), false);
const nextGeneration = Object.assign({}, stableAfter, {sceneGeneration: '2'});
assert.equal(shutter.sameGeometry(stableBefore, nextGeneration), false);
const nextGeometry = Object.assign({}, stableAfter, {geometryVersion: 5});
assert.equal(shutter.sameGeometry(stableBefore, nextGeometry), false);
const shiftedEye = Object.assign({}, stableAfter, {eye: [0, 0, 1]});
assert.equal(shutter.sameCamera(stableBefore, shiftedEye), false);
"""
    result = subprocess.run(
        [node, "-e", program, str(_SOURCE), json.dumps(_REPUBLISH)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
