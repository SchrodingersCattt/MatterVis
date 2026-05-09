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

from pathlib import Path

import pytest

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


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

    from crystal_viewer.app import create_app

    app = create_app()
    sync_cb = None
    for cb in app.callback_map.values():
        outputs = cb.get("output")
        # ``output`` may be a single Output or a list; we want the
        # callback whose output set covers ``camera-state-store.data``
        # AND ``agent-state-store.data``.
        rendered = repr(outputs)
        if "camera-state-store.data" in rendered and "agent-state-store.data" in rendered:
            sync_cb = cb
            break
    assert sync_cb is not None, "could not locate sync_agent_state in callback_map"
    source = inspect.getsource(sync_cb["callback"])
    assert "outputs[-1] = no_update" in source or "outputs[-1]=no_update" in source, (
        "sync_agent_state poll path must blank the camera-store slot. "
        "If you removed that line, the periodic-camera-snap-back bug "
        "is back. See file docstring."
    )
