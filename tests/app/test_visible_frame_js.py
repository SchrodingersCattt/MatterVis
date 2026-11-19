"""A blank or superseded figure must not replace the current scene."""

import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const gate = fs.readFileSync(process.argv[1] + '/mattervis.js', 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
function harness() {
  const expected = {scene_id: 'scene-a', scene_generation: 1, render_revision: 1,
    geometry_version: 1, display_version: 1, camera_revision: 1, camera_version: 1,
    server_started_at: '2026-10-25T10:00:00Z'};
  const frames = [];
  let hold = false, releaseFrame = null;
  const gd = {data: [], layout: {scene: {camera: {eye: {x: 1, y: 1, z: 1}}},
    meta: {mattervis_render: clone(expected)}}};
  function react(target, data) {
    const figure = Array.isArray(data) ? {data: data, layout: arguments[2]} : data;
    const traces = Array.isArray(figure.data) ? figure.data : [];
    function paint() {
      const render = figure.layout && figure.layout.meta && figure.layout.meta.mattervis_render;
      const images = figure.layout && Array.isArray(figure.layout.images) ? figure.layout.images : [];
      frames.push({
        scene: render ? render.scene_id : null,
        generation: render ? render.scene_generation : null,
        revision: render ? render.render_revision : null,
        traces: traces.length,
        images: images.length,
        image: images[0] ? images[0].source : null,
        names: traces.map(trace => trace && trace.name || ''),
        visible: traces.map(trace => !trace || trace.visible !== false),
        xLengths: traces.map(trace => trace && trace.x ? trace.x.length : 0),
      });
      target.layout = clone(figure.layout || {});
      target.data = traces;
      return target;
    }
    if (hold) {
      hold = false;
      return new Promise(resolve => { releaseFrame = () => resolve(paint()); });
    }
    return Promise.resolve(paint());
  }
  const sandbox = {console, Promise, Object, Array, JSON, Number, String, Date,
    setTimeout, clearTimeout,
    window: {Plotly: {react, newPlot: react, relayout: () => Promise.resolve(gd)}},
    document: {getElementById: id => id === 'crystal-graph' ? {querySelector: () => gd} :
      id === 'fast-view-metadata' ? {get textContent() { return JSON.stringify(expected); }} : null}};
  vm.createContext(sandbox);
  const gateEnd = gate.indexOf('  // ── Right-click menu');
  vm.runInContext(gate.slice(0, gateEnd) +
    'window.commitFrameExpectation = value => rememberExpectedRender(value, true);})();', sandbox);
  const trace = () => [{type: 'scatter3d', mode: 'markers', x: [0], y: [0], z: [0], visible: true}];
  return {frames, gd, expected, root: sandbox.window, trace,
    holdNext() { hold = true; },
    canRelease() { return typeof releaseFrame === 'function'; },
    release() { const finish = releaseFrame; releaseFrame = null; finish(); },
    commitExpectation() { sandbox.window.commitFrameExpectation(clone(expected)); },
    submit(scene, revision, data, extraLayout) {
      const extra = extraLayout || {};
      const layout = {scene: {camera: {eye: {x: 1, y: 1, z: 1}},
        uirevision: scene + '__name__' + revision + '__viewport'},
        meta: Object.assign({mattervis_render: Object.assign({}, expected, {scene_id: scene,
          render_revision: revision, camera_revision: revision, camera_version: revision})},
          extra.meta || {})};
      Object.keys(extra).forEach(key => { if (key !== 'meta') layout[key] = extra[key]; });
      return sandbox.window.Plotly.react(gd, {data: data, layout: layout});
    },
    submitUntyped(data) {
      return sandbox.window.Plotly.react(gd, {data: data, layout: {scene: {camera: {eye: {x: 1, y: 1, z: 1}}}}});
    }};
}
async function spinUntil(check) {
  for (let i = 0; i < 40 && !check(); i++) await Promise.resolve();
  assert.ok(check(), 'expected queued task did not start');
}
"""


def _run_node(scenario: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    assets = Path(__file__).resolve().parents[2] / "frontend" / "assets"
    program = _HARNESS + "\n(async () => {\n" + scenario + r"""
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", program, str(assets)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_untyped_empty_figure_does_not_clear_a_scene():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  await h.submitUntyped([]);
  assert.equal(h.frames.length, 1, 'untyped empty figure reached Plotly');
  assert.equal(h.gd.data.length, 1, 'untyped empty figure cleared the current scene');
  assert.equal(h.gd.data[0].x.length, 1);
""")


def test_current_empty_frame_is_the_final_intent():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  await h.submit('scene-a', 2, []);
  assert.equal(h.frames.length, 2, 'current empty scene did not reach Plotly');
  assert.equal(h.frames[1].revision, 2);
  assert.equal(h.frames[1].traces, 0);
  assert.equal(h.gd.data.length, 0, 'deleting the last atom left the previous trace');
""")


def test_duplicate_unchanged_frame_is_not_rebuilt():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  await h.submit('scene-a', 1, h.trace());
  assert.equal(h.frames.length, 1, 'unchanged scene rebuilt the canvas');
  assert.equal(h.frames[0].scene, 'scene-a');
  assert.equal(h.frames[0].traces, 1);
""")


def test_older_revision_does_not_replace_the_current_frame():
    _run_node(r"""
  const h = harness();
  h.expected.render_revision = 4;
  h.expected.camera_revision = 4;
  await h.submit('scene-a', 1, h.trace());
  assert.equal(h.frames.length, 0, 'older revision reached Plotly');
  assert.equal(h.gd.data.length, 0, 'older revision painted over the current scene');
""")


def test_queued_older_frame_yields_to_the_newer_frame_for_the_same_scene():
    _run_node(r"""
  const h = harness();
  let release;
  const blocker = h.root.MatterVisRenderQueue.enqueue(h.gd,
    () => new Promise(resolve => { release = resolve; }));
  await spinUntil(() => typeof release === 'function');
  const older = h.submit('scene-a', 2, h.trace());
  const empty = h.submit('scene-a', 3, []);
  const newer = h.submit('scene-a', 4, h.trace());
  release();
  await older;
  await empty;
  await newer;
  await blocker;
  assert.deepEqual(h.frames.map(frame => frame.revision), [4],
    'a queued older or empty frame still reached Plotly');
  assert.equal(h.gd.data.length, 1);
  assert.equal(h.gd.layout.meta.mattervis_render.render_revision, 4);
""")


def test_scene_switch_keeps_the_final_scene_when_the_previous_frame_is_still_queued():
    _run_node(r"""
  const h = harness();
  let release;
  const blocker = h.root.MatterVisRenderQueue.enqueue(h.gd,
    () => new Promise(resolve => { release = resolve; }));
  await spinUntil(() => typeof release === 'function');
  const previous = h.submit('scene-a', 2, h.trace());
  h.expected.scene_id = 'scene-b';
  h.expected.render_revision = 2;
  h.expected.camera_revision = 2;
  const current = h.submit('scene-b', 2, h.trace());
  const stale = h.submit('scene-a', 1, h.trace());
  release();
  await previous;
  await current;
  await stale;
  await blocker;
  assert.deepEqual(h.frames.map(frame => frame.scene), ['scene-b'],
    'the previous scene painted after the selected scene changed');
  assert.equal(h.gd.layout.meta.mattervis_render.scene_id, 'scene-b');
  assert.equal(h.gd.data.length, 1);
""")


def test_queued_empty_scene_replaces_a_pending_atom_frame():
    _run_node(r"""
  const h = harness();
  let release;
  const blocker = h.root.MatterVisRenderQueue.enqueue(h.gd,
    () => new Promise(resolve => { release = resolve; }));
  await spinUntil(() => typeof release === 'function');
  const atoms = h.submit('scene-a', 2, h.trace());
  const emptied = h.submit('scene-a', 3, []);
  release();
  await atoms;
  await emptied;
  await blocker;
  assert.deepEqual(h.frames.map(frame => frame.revision), [3],
    'the atom frame painted after the empty scene became current');
  assert.equal(h.gd.data.length, 0);
""")


def test_scene_switch_to_an_empty_scene_drops_the_previous_atoms():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 2, h.trace());
  h.expected.scene_id = 'scene-b';
  h.expected.scene_generation = 2;
  h.expected.render_revision = 1;
  h.expected.camera_revision = 1;
  h.commitExpectation();
  await h.submit('scene-b', 1, []);
  assert.equal(h.frames.length, 2, 'cross-scene empty frame did not reach Plotly');
  assert.deepEqual([h.frames[1].scene, h.frames[1].generation, h.frames[1].traces],
    ['scene-b', 2, 0]);
  assert.equal(h.gd.data.length, 0, 'the previous scene remained after the selected scene became empty');
  assert.equal(h.gd.layout.meta.mattervis_render.scene_id, 'scene-b');
""")


def test_hidden_atoms_keep_a_visible_cell_layer():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  const hidden = [{type: 'scatter3d', name: 'atom', x: [0], y: [0], z: [0], visible: false}];
  const cell = [{type: 'scatter3d', name: 'unit-cell-box', x: [0, 1], y: [0, 0], z: [0, 0], visible: true}];
  await h.submit('scene-a', 2, hidden.concat(cell));
  assert.equal(h.frames.length, 2);
  assert.deepEqual(h.frames[1].names, ['atom', 'unit-cell-box']);
  assert.deepEqual(h.frames[1].visible, [false, true]);
  assert.equal(h.gd.data[0].visible, false, 'hiding the atom dropped the committed frame');
  assert.equal(h.gd.data[1].name, 'unit-cell-box');
""")


def test_publication_image_applies_without_a_visible_atom_trace():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  const anchor = [{type: 'scatter3d', name: 'flat-ortep-camera', x: [0], y: [0], z: [0],
    opacity: 0, visible: true}];
  const image = {source: 'data:image/png;base64,QQ==', xref: 'paper', yref: 'paper'};
  await h.submit('scene-a', 2, anchor, {images: [image]});
  assert.equal(h.frames.length, 2, 'publication image did not reach Plotly');
  assert.equal(h.frames[1].image, image.source);
  assert.equal(h.gd.layout.images[0].source, image.source);
  assert.equal(h.gd.data[0].opacity, 0);
""")


def test_image_only_frame_replaces_atom_traces():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  const image = {source: 'data:image/png;base64,QQ==', xref: 'paper', yref: 'paper'};
  await h.submit('scene-a', 2, [], {images: [image]});
  assert.equal(h.frames.length, 2, 'image-only frame did not reach Plotly');
  assert.equal(h.frames[1].traces, 0);
  assert.equal(h.frames[1].image, image.source);
  assert.equal(h.gd.data.length, 0, 'image-only frame left the previous atom trace');
  assert.equal(h.gd.layout.images[0].source, image.source);
""")


def test_overlay_frame_applies_when_no_atom_trace_is_visible():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  const overlay = [{type: 'mesh3d', name: 'overlay', x: [0, 1, 0], y: [0, 0, 1], z: [0, 0, 0],
    i: [0], j: [1], k: [2], visible: true}];
  await h.submit('scene-a', 2, overlay);
  assert.equal(h.frames.length, 2);
  assert.deepEqual(h.frames[1].names, ['overlay']);
  assert.equal(h.gd.data.length, 1);
  assert.equal(h.gd.data[0].name, 'overlay');
""")


def test_stale_empty_frame_does_not_clear_the_current_scene():
    _run_node(r"""
  const h = harness();
  h.expected.render_revision = 4;
  h.expected.camera_revision = 4;
  await h.submit('scene-a', 4, h.trace());
  await h.submit('scene-a', 1, []);
  assert.equal(h.frames.length, 1, 'stale empty frame reached Plotly');
  assert.equal(h.gd.data.length, 1, 'stale empty frame cleared the current scene');
""")


def test_all_hidden_and_empty_selection_replace_the_previous_atoms():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  const hidden = [{type: 'scatter3d', name: 'atom', x: [0], y: [0], z: [0], visible: false}];
  await h.submit('scene-a', 2, hidden);
  assert.equal(h.frames.length, 2, 'all-hidden frame did not reach Plotly');
  assert.deepEqual(h.frames[1].visible, [false]);
  assert.equal(h.gd.data[0].visible, false);
  const selection = [{type: 'scatter3d', name: 'selection', x: [], y: [], z: [], visible: true}];
  await h.submit('scene-a', 3, selection);
  assert.equal(h.frames.length, 3, 'empty-x selection frame did not reach Plotly');
  assert.deepEqual(h.frames[2].xLengths, [0]);
  assert.equal(h.gd.data.length, 1);
  assert.equal(h.gd.data[0].name, 'selection');
  assert.equal(h.gd.data[0].x.length, 0);
""")


def test_new_generation_empty_frame_clears_the_same_scene():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 4, h.trace());
  h.expected.scene_generation = 2;
  h.expected.render_revision = 1;
  h.expected.camera_revision = 1;
  h.commitExpectation();
  await h.submit('scene-a', 1, []);
  assert.equal(h.frames.length, 2, 'new generation empty frame did not reach Plotly');
  assert.equal(h.frames[1].generation, 2);
  assert.equal(h.frames[1].traces, 0);
  assert.equal(h.gd.data.length, 0, 'new generation left the previous atoms');
  assert.equal(h.gd.layout.meta.mattervis_render.scene_generation, 2);
""")


def test_inflight_scene_finishes_and_the_newer_empty_intent_remains():
    _run_node(r"""
  const h = harness();
  h.holdNext();
  const stale = h.submit('scene-a', 2, h.trace());
  await spinUntil(() => h.canRelease());
  h.expected.scene_id = 'scene-b';
  h.expected.scene_generation = 2;
  h.expected.render_revision = 1;
  h.expected.camera_revision = 1;
  h.commitExpectation();
  const emptied = h.submit('scene-b', 1, []);
  h.release();
  await stale;
  await emptied;
  assert.equal(h.frames.length, 2, 'the issued react or the empty successor did not finish');
  assert.equal(h.frames[0].scene, 'scene-a');
  assert.equal(h.frames[0].traces, 1, 'the issued react did not write before the successor');
  assert.deepEqual([h.frames[1].scene, h.frames[1].generation, h.frames[1].traces],
    ['scene-b', 2, 0]);
  assert.equal(h.gd.data.length, 0, 'the finished stale react stayed on screen');
  assert.equal(h.gd.layout.meta.mattervis_render.scene_id, 'scene-b');
""")


def test_loading_placeholder_does_not_replace_a_committed_frame():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 2, h.trace());
  await h.submit('scene-a', 3, [], {meta: {mattervis_pending: true}});
  assert.equal(h.frames.length, 1, 'loading placeholder replaced the committed frame');
  assert.equal(h.gd.data.length, 1);
  await h.submit('scene-a', 4, []);
  assert.equal(h.frames.length, 2, 'committed empty frame did not replace the picture after a placeholder');
  assert.equal(h.gd.data.length, 0);
  h.gd.layout = {};
  h.gd.data = [];
  h.frames.length = 0;
  await h.submit('scene-a', 5, [], {meta: {mattervis_pending: true}});
  assert.equal(h.frames.length, 1, 'loading placeholder could not paint a blank canvas');
  assert.equal(h.gd.layout.meta.mattervis_pending, true);
""")


def test_same_revision_new_generation_replaces_a_delivered_frame():
    _run_node(r"""
  const h = harness();
  await h.submit('scene-a', 1, h.trace());
  const kept = h.gd.layout.scene.uirevision;
  h.expected.scene_generation = 2;
  h.commitExpectation();
  await h.submit('scene-a', 1, []);
  assert.equal(h.gd.layout.scene.uirevision, kept, 'the regression changed uirevision');
  assert.equal(h.frames.length, 2, 'new generation empty frame matched the delivered key');
  assert.equal(h.frames[1].generation, 2);
  assert.equal(h.frames[1].revision, 1);
  assert.equal(h.frames[1].traces, 0);
  assert.equal(h.gd.data.length, 0, 'generation 2 empty frame left generation 1');
  const replacement = [{type: 'scatter3d', name: 'restored', x: [2], y: [0], z: [0], visible: true}];
  h.expected.scene_generation = 3;
  h.commitExpectation();
  await h.submit('scene-a', 1, replacement);
  assert.equal(h.gd.layout.scene.uirevision, kept);
  assert.equal(h.frames.length, 3, 'new generation geometry matched the delivered key');
  assert.equal(h.frames[2].generation, 3);
  assert.equal(h.gd.data[0].name, 'restored');
  assert.equal(h.gd.layout.meta.mattervis_render.scene_generation, 3);
""")


def test_queued_same_revision_new_generation_is_the_last_intent():
    _run_node(r"""
  const h = harness();
  let release;
  const blocker = h.root.MatterVisRenderQueue.enqueue(h.gd,
    () => new Promise(resolve => { release = resolve; }));
  await spinUntil(() => typeof release === 'function');
  const older = h.submit('scene-a', 1, h.trace());
  h.expected.scene_generation = 2;
  h.commitExpectation();
  const emptied = h.submit('scene-a', 1, []);
  release();
  await older;
  await emptied;
  await blocker;
  assert.deepEqual(h.frames.map(frame => [frame.generation, frame.revision, frame.traces]),
    [[2, 1, 0]], 'queued generation 1 painted after generation 2');
  assert.equal(h.gd.layout.scene.uirevision, 'scene-a__name__1__viewport');
  assert.equal(h.gd.data.length, 0);
""")


def test_inflight_same_revision_new_generation_remains_after_the_first_write():
    _run_node(r"""
  const h = harness();
  h.holdNext();
  const first = h.submit('scene-a', 1, h.trace());
  await spinUntil(() => h.canRelease());
  h.expected.scene_generation = 2;
  h.commitExpectation();
  const emptied = h.submit('scene-a', 1, []);
  h.release();
  await first;
  await emptied;
  assert.equal(h.frames.length, 2, 'generation 2 was treated as the in-flight generation 1 frame');
  assert.deepEqual([h.frames[0].generation, h.frames[0].traces], [1, 1]);
  assert.deepEqual([h.frames[1].generation, h.frames[1].revision, h.frames[1].traces], [2, 1, 0]);
  assert.equal(h.gd.layout.scene.uirevision, 'scene-a__name__1__viewport');
  assert.equal(h.gd.data.length, 0, 'the finished generation 1 frame stayed on screen');
  h.holdNext();
  h.expected.scene_generation = 3;
  h.commitExpectation();
  const second = h.submit('scene-a', 1, h.trace());
  await spinUntil(() => h.canRelease());
  h.expected.scene_generation = 4;
  h.commitExpectation();
  const restored = [{type: 'scatter3d', name: 'restored', x: [3], y: [0], z: [0], visible: true}];
  const next = h.submit('scene-a', 1, restored);
  h.release();
  await second;
  await next;
  assert.equal(h.frames.at(-1).generation, 4);
  assert.equal(h.frames.at(-1).traces, 1);
  assert.equal(h.gd.data[0].name, 'restored');
  assert.equal(h.gd.layout.scene.uirevision, 'scene-a__name__1__viewport');
  assert.equal(h.gd.layout.meta.mattervis_render.scene_generation, 4);
""")
