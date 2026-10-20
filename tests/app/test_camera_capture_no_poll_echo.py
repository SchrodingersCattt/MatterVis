"""Regression: a user-driven camera change must not survive into the
next ``agent-state-poll`` round and snap the user's view back.

Pre-fix flow (every 5 s after any camera drag):

    user drags --(relayoutData)--> capture_camera
        --(patch_state arms pending_state unconditionally)-->
    +5 s: agent-state-poll --(sync_agent_state)-->
        pop_pending_state --> writes camera-state-store with
        whatever camera was captured at debounce edge --> update_view
        rebuilds figure with that camera --> view "snaps back"

Two-layer fix this test guards:

1. ``ViewerBackend.patch_state`` accepts ``broadcast=False``; when the
   call originates from the browser (``capture_camera``) the camera
   write must NOT arm ``pending_state``. This is the load-bearing fix.

2. ``sync_agent_state``'s poll path emits ``no_update`` for the
   ``camera-state-store`` Output even when ``pending_state`` is non-
   None, so a REST-driven state change can never re-snap the camera.
   This is defence-in-depth.

DO NOT REMOVE -- this guards a deeply confusing periodic-snapback bug.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.presets import default_preset_path


CAMERA_A = {
    "eye": {"x": 1.5, "y": 0.0, "z": 0.0},
    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
}
CAMERA_B = {
    "eye": {"x": 0.0, "y": 1.5, "z": 0.0},
    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
    "up": {"x": 0.0, "y": 0.0, "z": 1.0},
}


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))


def test_patch_state_with_broadcast_false_does_not_arm_pending_state(backend: ViewerBackend) -> None:
    """The load-bearing fix: a UI-originated camera patch must not
    cause ``pop_pending_state()`` to return non-None on the next poll."""
    backend.pop_pending_state()  # drain any startup pending state

    backend.patch_state({"camera": CAMERA_A}, broadcast=False)

    assert backend.pop_pending_state() is None, (
        "patch_state(..., broadcast=False) MUST NOT arm pending_state -- "
        "if it does, the next agent-state-poll will echo the camera back "
        "into camera-state-store and the user's view will snap back."
    )


def test_patch_state_default_broadcast_still_arms_pending_state(backend: ViewerBackend) -> None:
    """REST and WebSocket callers depend on broadcast=True default."""
    backend.pop_pending_state()

    backend.patch_state({"display_mode": "cluster"})

    pending = backend.pop_pending_state()
    assert pending is not None, "default broadcast=True must arm pending_state for REST/WS callers"
    assert pending.get("display_mode") == "cluster"


def test_repeated_camera_drags_never_arm_pending_state(backend: ViewerBackend) -> None:
    """Simulate ten Plotly relayout events in a row; pending_state must
    stay None throughout (no scheduled UI echo means no snap-back)."""
    backend.pop_pending_state()

    cameras = [
        {
            "eye": {"x": 1.5 + 0.1 * i, "y": 0.0, "z": 0.0},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        }
        for i in range(10)
    ]
    for cam in cameras:
        backend.patch_state({"camera": cam}, broadcast=False)

    assert backend.pop_pending_state() is None


def test_camera_still_persists_per_scene_with_broadcast_false(backend: ViewerBackend) -> None:
    """``broadcast=False`` skips the *UI echo*; it MUST still persist
    the camera into the scene store so per-tab camera state survives a
    scene switch (regression for tests/app/test_camera_persistence_per_tab.py)."""
    scene_a = backend.active_scene_id()
    scene_b = backend.create_scene(structure=backend.get_state()["structure"], label="Second")["id"]

    backend.patch_state({"camera": CAMERA_A}, scene_id=scene_a, broadcast=False)
    backend.patch_state({"camera": CAMERA_B}, scene_id=scene_b, broadcast=False)

    assert backend.get_state(scene_a)["camera"] == CAMERA_A
    assert backend.get_state(scene_b)["camera"] == CAMERA_B


def test_sync_agent_state_poll_path_returns_no_update_for_camera_store() -> None:
    """Defence-in-depth: even when an external (REST/WS) state change
    legitimately arms ``pending_state`` and the poll picks it up, the
    camera-store output slot must be ``no_update`` so the browser-owned
    camera is never overwritten by stale stored values.

    We invoke ``sync_agent_state`` indirectly by inspecting the source
    of the assembled callback: simulating Dash's callback dispatch
    machinery in a unit test is fragile, but reading the source ensures
    the ``outputs[-1] = no_update`` line stays in the poll branch.
    """
    import inspect

    from mat_viewer.app import create_app

    app = create_app()
    sync_cb = None
    for cb in app.callback_map.values():
        outputs = cb.get("output")
        # ``output`` may be a single Output or a list; we want the
        # callback whose output set covers ``camera-state-store.data``
        # AND ``agent-state-store.data``.
        rendered = repr(cb)
        if (
            "camera-state-store.data" in rendered
            and "agent-state-store.data" in rendered
            and "agent-state-poll" in rendered
        ):
            sync_cb = cb
            break
    assert sync_cb is not None, "could not locate sync_agent_state in callback_map"
    source = inspect.getsource(sync_cb["callback"])
    assert "outputs[-1] = no_update" in source or "outputs[-1]=no_update" in source, (
        "sync_agent_state poll path must blank the camera-store slot. "
        "If you removed that line, the periodic-camera-snap-back bug "
        "is back. See file docstring."
    )


def test_camera_capture_drops_late_relayout_from_previous_scene() -> None:
    """A delayed Plotly relayout must not be attributed to the new tab.

    ``relayoutData`` has no scene id of its own.  The clientside bridge uses
    the canvas metadata plus the scene that owned the last pointer gesture;
    keeping that second marker prevents an event delivered after a tab switch
    from persisting the old scene's camera on the newly painted scene.
    """
    callback_source = (
        Path(__file__).resolve().parents[2] / "mat_viewer" / "app" / "callbacks_view.py"
    ).read_text(encoding="utf-8")
    assert "__mvLastGestureSceneId" in callback_source
    assert "lastGestureId !== canvasId" in callback_source

    mattervis = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    source = mattervis.read_text(encoding="utf-8")
    assert "window.__mvLastGestureSceneId = sceneId" in source


def test_camera_capture_wheel_after_scene_switch_refreshes_scene_fence() -> None:
    """A wheel-only gesture on the new tab must capture that tab's camera."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    callbacks = (
        Path(__file__).resolve().parents[2] / "mat_viewer" / "app" / "callbacks_view.py"
    ).read_text(encoding="utf-8")
    start = callbacks.index('            """\n            function(relayout) {')
    start = callbacks.index("function(relayout)", start)
    end = callbacks.index('            """', start)
    capture_callback = textwrap.dedent(callbacks[start:end]).strip()
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = f"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const callbackSource={json.dumps(capture_callback)};
const listeners={{}};
function add(name, fn) {{ (listeners[name] ||= []).push(fn); }}
function emit(name, event) {{ (listeners[name] || []).forEach(fn => fn(event)); }}
function layout(scene) {{ return {{meta:{{mattervis_render:{{scene_id:scene,render_revision:1,server_started_at:'epoch'}}}}}}; }}
const gd={{layout:layout('a'),_fullLayout:{{scene:{{camera:{{}}}}}}}};
const graph={{querySelector:()=>gd,querySelectorAll:()=>[]}};
const document={{readyState:'loading',addEventListener:add,querySelector:()=>gd,
  querySelectorAll:()=>[],getElementById:id=>id==='crystal-graph'?graph:null,
  body:{{prepend(){{}},appendChild(){{}}}},documentElement:{{}}}};
function WebSocketMock() {{ this.addEventListener=()=>{{}}; }}
const MutationObserver=function() {{}}; MutationObserver.prototype.observe=function(){{}}; MutationObserver.prototype.disconnect=function(){{}};
const sandbox={{console,Promise,JSON,Number,Date,Object,Array,String,Math,setTimeout:()=>0,clearTimeout:()=>{{}},
  MutationObserver,requestAnimationFrame:()=>0,document,WebSocket:WebSocketMock,
  window:{{Plotly:{{react:()=>Promise.resolve(gd),newPlot:()=>Promise.resolve(gd),update:()=>Promise.resolve(gd),relayout:()=>Promise.resolve(gd),restyle:()=>Promise.resolve(gd)}},WebSocket:WebSocketMock,
    location:{{protocol:'http:',host:'localhost'}},addEventListener(){{}},dash_clientside:{{no_update:{{__no_update:true}},set_props(){{}}}}}}}};
vm.createContext(sandbox);
vm.runInContext(source.slice(0,source.indexOf('/* Live compass overlay')),sandbox);
gd.layout=layout('a');
emit('pointerdown',{{target:{{closest:()=>graph}}}});
emit('pointerup',{{}});
gd.layout=layout('b');
// Reset/Fit may have advanced the authoritative camera clock through the
// camera-only relayout path without rewriting figure metadata. The transient
// revision is only authoritative for the scene:generation scope that wrote it.
gd.__mvCameraRevisionScope='b:';
gd.__mvCameraRevision=2;
emit('wheel',{{target:{{closest:()=>graph}}}});
assert.equal(sandbox.window.__mvLastGestureSceneId,'b','wheel did not refresh the scene fence');
const capture=vm.runInNewContext('('+callbackSource+')',sandbox);
const result=capture({{'scene.camera':{{eye:{{x:1}}}}}});
assert.equal(result.scene_id,'b','wheel camera relayout was attributed to the old scene');
assert.equal(result.camera_revision,2,'wheel stamped the stale canvas camera revision');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_camera_capture_wheel_fence_does_not_survive_scene_switch() -> None:
    """A wheel stamp from A must not reject a later camera command on B."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    callbacks = (
        Path(__file__).resolve().parents[2] / "mat_viewer" / "app" / "callbacks_view.py"
    ).read_text(encoding="utf-8")
    start = callbacks.index('            """\n            function(relayout) {')
    start = callbacks.index("function(relayout)", start)
    end = callbacks.index('            """', start)
    capture_callback = textwrap.dedent(callbacks[start:end]).strip()
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = f"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const callbackSource={json.dumps(capture_callback)};
const listeners={{}};
function add(name, fn) {{ (listeners[name] ||= []).push(fn); }}
function emit(name, event) {{ (listeners[name] || []).forEach(fn => fn(event)); }}
function layout(scene) {{ return {{meta:{{mattervis_render:{{scene_id:scene,render_revision:1,scene_generation:scene==='a'?1:2,camera_revision:0,server_started_at:'epoch'}}}}}}; }}
const gd={{layout:layout('a'),_fullLayout:{{scene:{{camera:{{}}}}}}}};
const graph={{querySelector:()=>gd,querySelectorAll:()=>[]}};
const document={{readyState:'loading',addEventListener:add,querySelector:()=>gd,
  querySelectorAll:()=>[],getElementById:id=>id==='crystal-graph'?graph:null,
  body:{{prepend(){{}},appendChild(){{}}}},documentElement:{{}}}};
function WebSocketMock() {{ this.addEventListener=()=>{{}}; }}
const MutationObserver=function() {{}}; MutationObserver.prototype.observe=function(){{}}; MutationObserver.prototype.disconnect=function(){{}};
const sandbox={{console,Promise,JSON,Number,Date,Object,Array,String,Math,setTimeout:()=>0,clearTimeout:()=>{{}},
  MutationObserver,requestAnimationFrame:()=>0,document,WebSocket:WebSocketMock,
  window:{{Plotly:{{react:()=>Promise.resolve(gd),newPlot:()=>Promise.resolve(gd),update:()=>Promise.resolve(gd),relayout:()=>Promise.resolve(gd),restyle:()=>Promise.resolve(gd)}},WebSocket:WebSocketMock,
    location:{{protocol:'http:',host:'localhost'}},addEventListener(){{}},dash_clientside:{{no_update:{{__no_update:true}},set_props(){{}}}}}}}};
vm.createContext(sandbox);
vm.runInContext(source.slice(0,source.indexOf('/* Live compass overlay')),sandbox);
emit('wheel',{{target:{{closest:()=>graph}}}});
gd.layout=layout('b');
const capture=vm.runInNewContext('('+callbackSource+')',sandbox);
const result=capture({{'scene.camera':{{eye:{{x:99}}}}}});
assert.equal(result.scene_id,'b','wheel fence from A survived the B scene switch');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_camera_revision_stamp_is_scoped_to_the_live_scene() -> None:
    """A camera revision left on the graph div must not stamp the next scene."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    callbacks = (
        Path(__file__).resolve().parents[2] / "mat_viewer" / "app" / "callbacks_view.py"
    ).read_text(encoding="utf-8")
    start = callbacks.index('            """\n            function(relayout) {')
    start = callbacks.index("function(relayout)", start)
    end = callbacks.index('            """', start)
    capture_callback = textwrap.dedent(callbacks[start:end]).strip()
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = f"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const callbackSource={json.dumps(capture_callback)};
const listeners={{}};
function add(name, fn) {{ (listeners[name] ||= []).push(fn); }}
function emit(name, event) {{ (listeners[name] || []).forEach(fn => fn(event)); }}
function layout(scene, generation, revision) {{
  return {{meta:{{mattervis_render:{{scene_id:scene,render_revision:1,scene_generation:generation,camera_revision:revision,server_started_at:'epoch'}}}}}};
}}
const gd={{layout:layout('a',7,1),_fullLayout:{{scene:{{camera:{{}}}}}}}};
const graph={{querySelector:()=>gd,querySelectorAll:()=>[]}};
const document={{readyState:'loading',addEventListener:add,querySelector:()=>gd,
  querySelectorAll:()=>[],getElementById:id=>id==='crystal-graph'?graph:null,
  body:{{prepend(){{}},appendChild(){{}}}},documentElement:{{}}}};
function WebSocketMock() {{ this.addEventListener=()=>{{}}; }}
const MutationObserver=function() {{}}; MutationObserver.prototype.observe=function(){{}}; MutationObserver.prototype.disconnect=function(){{}};
const sandbox={{console,Promise,JSON,Number,Date,Object,Array,String,Math,setTimeout:()=>0,clearTimeout:()=>{{}},
  MutationObserver,requestAnimationFrame:()=>0,document,WebSocket:WebSocketMock,
  window:{{Plotly:{{react:()=>Promise.resolve(gd),newPlot:()=>Promise.resolve(gd),update:()=>Promise.resolve(gd),relayout:()=>Promise.resolve(gd),restyle:()=>Promise.resolve(gd)}},WebSocket:WebSocketMock,
    location:{{protocol:'http:',host:'localhost'}},addEventListener(){{}},dash_clientside:{{no_update:{{__no_update:true}},set_props(){{}}}}}}}};
vm.createContext(sandbox);
vm.runInContext(source.slice(0,source.indexOf('/* Live compass overlay')),sandbox);
gd.__mvCameraRevisionScope='a:7';
gd.__mvCameraRevision=4;
gd.layout=layout('b',8,0);
emit('pointerdown',{{target:{{closest:()=>graph}}}});
const capture=vm.runInNewContext('('+callbackSource+')',sandbox);
const dragged=capture({{'scene.camera':{{eye:{{x:1}}}}}});
assert.equal(dragged.scene_id,'b');
assert.equal(dragged.scene_generation,8);
assert.equal(dragged.camera_revision,0,'drag reused scene A camera revision');
gd.__mvCameraRevisionScope='a:7';
gd.__mvCameraRevision=4;
emit('wheel',{{target:{{closest:()=>graph}}}});
const zoomed=capture({{'scene.camera':{{eye:{{x:2}}}}}});
assert.equal(zoomed.scene_id,'b');
assert.equal(zoomed.scene_generation,8);
assert.equal(zoomed.camera_revision,0,'wheel reused scene A camera revision');
gd.__mvCameraRevisionScope='a:7';
gd.__mvCameraRevision=4;
gd.layout=layout('a',7,5);
emit('pointerdown',{{target:{{closest:()=>graph}}}});
const refreshed=capture({{'scene.camera':{{eye:{{x:3}}}}}});
assert.equal(refreshed.scene_id,'a');
assert.equal(refreshed.scene_generation,7);
assert.equal(refreshed.camera_revision,5,'stale transient revision beat the full frame');
emit('wheel',{{target:{{closest:()=>graph}}}});
const refreshedZoom=capture({{'scene.camera':{{eye:{{x:4}}}}}});
assert.equal(refreshedZoom.camera_revision,5,'wheel kept the stale transient revision');
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_expired_wheel_relayout_is_not_retargeted_at_the_next_scene() -> None:
    """A wheel timeout must keep the fence that stops a late relayout landing on B."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the frontend regression")
    callbacks = (
        Path(__file__).resolve().parents[2] / "mat_viewer" / "app" / "callbacks_view.py"
    ).read_text(encoding="utf-8")
    start = callbacks.index('            """\n            function(relayout) {')
    start = callbacks.index("function(relayout)", start)
    end = callbacks.index('            """', start)
    capture_callback = textwrap.dedent(callbacks[start:end]).strip()
    source = Path(__file__).resolve().parents[2] / "frontend" / "assets" / "mattervis.js"
    program = f"""
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
const callbackSource={json.dumps(capture_callback)};
const listeners={{}};
const timers=[];
function add(name, fn) {{ (listeners[name] ||= []).push(fn); }}
function emit(name, event) {{ (listeners[name] || []).forEach(fn => fn(event)); }}
function layout(scene) {{
  return {{meta:{{mattervis_render:{{scene_id:scene,render_revision:1,scene_generation:scene==='a'?1:2,camera_revision:0,server_started_at:'epoch'}}}}}};
}}
const gd={{layout:layout('a'),_fullLayout:{{scene:{{camera:{{}}}}}}}};
const graph={{querySelector:()=>gd,querySelectorAll:()=>[]}};
const document={{readyState:'loading',addEventListener:add,querySelector:()=>gd,
  querySelectorAll:()=>[],getElementById:id=>id==='crystal-graph'?graph:null,
  body:{{prepend(){{}},appendChild(){{}}}},documentElement:{{}}}};
function WebSocketMock() {{ this.addEventListener=()=>{{}}; }}
const MutationObserver=function() {{}}; MutationObserver.prototype.observe=function(){{}}; MutationObserver.prototype.disconnect=function(){{}};
const sandbox={{console,Promise,JSON,Number,Date,Object,Array,String,Math,setTimeout:(fn)=>{{timers.push(fn); return timers.length;}},clearTimeout:()=>{{}},
  MutationObserver,requestAnimationFrame:()=>0,document,WebSocket:WebSocketMock,
  window:{{Plotly:{{react:()=>Promise.resolve(gd),newPlot:()=>Promise.resolve(gd),update:()=>Promise.resolve(gd),relayout:()=>Promise.resolve(gd),restyle:()=>Promise.resolve(gd)}},WebSocket:WebSocketMock,
    location:{{protocol:'http:',host:'localhost'}},addEventListener(){{}},dash_clientside:{{no_update:{{__no_update:true}},set_props(){{}}}}}}}};
vm.createContext(sandbox);
vm.runInContext(source.slice(0,source.indexOf('/* Live compass overlay')),sandbox);
const queued=timers.length;
emit('wheel',{{target:{{closest:()=>graph}}}});
assert.equal(sandbox.window.__mvGestureSceneGeneration,1);
assert.equal(sandbox.window.__mvGestureCameraRevision,0);
timers.slice(queued).forEach((fn)=>{{ try {{ fn(); }} catch (error) {{}} }});
gd.layout=layout('b');
const capture=vm.runInNewContext('('+callbackSource+')',sandbox);
const result=capture({{'scene.camera':{{eye:{{x:9}}}}}});
assert.equal(result,sandbox.window.dash_clientside.no_update,'expired A wheel was stored on B');
assert.equal(sandbox.window.__mvLastGestureSceneId,'a');
assert.equal(sandbox.window.__mvGestureSceneGeneration,1);
assert.equal(sandbox.window.__mvGestureCameraRevision,0);
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_broadcast_state_survives_following_ui_camera_write(backend: ViewerBackend) -> None:
    """A UI camera persist must not erase an already queued agent update."""
    backend.pop_pending_state()
    backend.patch_state({"atom_scale": 1.25}, broadcast=True)
    backend.patch_state({"camera": CAMERA_B}, broadcast=False)

    pending = backend.pop_pending_state()
    assert pending is not None
    assert pending["atom_scale"] == pytest.approx(1.25)
    assert pending["camera"] == CAMERA_B
