"""Feed real mixed REST envelopes to the unmodified browser submitter."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from mat_viewer.app import create_app


def test_mixed_rest_envelopes_do_not_dedupe_camera_or_completed_frame(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the delivery regression")
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        backend = app.crystal_backend
        backend.patch_state({"camera": {
            "eye": {"x": 1.4, "y": 1.2, "z": 1.1},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "perspective"},
        }}, broadcast=False)
        before = backend.get_state()
        cursor = backend.latest_figure_seq()
        response = app.server.test_client().post("/api/v2/state", json={
            "projection": "orthographic", "minor_opacity": before["minor_opacity"] + 0.1,
        })
        assert response.status_code == 200
        state = response.get_json()
        updates = backend.figure_broadcasts_since(cursor)
        assert [update["update_kind"] for update in updates] == ["display", "camera"]
        publish = next(
            spec["callback"].__wrapped__
            for spec in app.callback_map.values()
            if spec.get("callback") and spec["callback"].__name__ == "publish_confirmed_state"
        )
        confirmed = publish(state)
        figure = {"data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
                  "layout": {"scene": {"camera": state["camera"]}}}
        backend._stamp_figure_render_metadata(figure, state)
        full = backend.broadcast_figure(scene_id=state["scene_id"], figure=figure, state=state)
        fixture = {"before": before, "updates": updates, "confirmed": confirmed, "full": full}
    finally:
        app.close_extensions()
        app.crystal_backend.close()

    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "view_updates.js"
    program = r"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8'), fixture=JSON.parse(fs.readFileSync(0,'utf8'));
async function run(displayCompletesFirst) {
  const cameras=[], frames=[], restyles=[];
  let releaseDisplay;
  const gd={layout:{scene:{camera:fixture.before.camera},meta:{mattervis_render:fixture.before}},
    data:[{type:'scatter3d',meta:{mv_role:'atom',mv_minor:true},x:[0],y:[0],z:[0]}]};
  const sandbox={console,Promise,Number,String,Object,Array,JSON,window:{
    MatterVisViewCamera:{graph:()=>gd,applyAsync:camera=>{cameras.push(camera);return Promise.resolve();}},
    MatterVisViewCompass:{setEnabled:()=>true},
    Plotly:{
      restyle:(_gd,patch)=>{restyles.push(patch);return new Promise(resolve=>{releaseDisplay=resolve;});},
      react:(_gd,data,layout)=>{frames.push({data,layout});gd.layout=layout;return Promise.resolve();},
    },
  }};
  vm.createContext(sandbox);vm.runInContext(source,sandbox);
  const submit=sandbox.window.MatterVisViewUpdates.submit;
  const display=submit(fixture.updates[0]);
  assert.equal(restyles.length,1,'opacity envelope did not reach the display owner');
  if (displayCompletesFirst) {releaseDisplay();await display;}
  const camera=submit(fixture.updates[1]);
  assert.equal(cameras.length,1,'camera was deduplicated against the display envelope');
  assert.equal(cameras[0].projection.type,'orthographic');
  if (!displayCompletesFirst) {releaseDisplay();await display;}
  assert.equal(await camera,'camera');
  assert.equal(submit(fixture.confirmed),'stale','same-kind Dash confirmation was not deduplicated');
  assert.equal(await submit(fixture.full),'figure','completed frame collided with a typed envelope');
  assert.equal(frames.length,1);
  assert.equal(submit(fixture.full),'stale','completed frame was not deduplicated');
}
(async()=>{await run(false);await run(true);})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", program, str(source)], input=json.dumps(fixture),
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
