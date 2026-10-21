"""Regression guards for scene-scoped callback state.

The browser can deliver a periodic state poll while a scene-tab switch or
upload is still updating ``scene-tabs.value``.  ``pending_state`` therefore
may describe a different scene than the callback's current ``scene_id``.
Controls from that snapshot must never be written into the selected tab.
"""
from __future__ import annotations

from types import SimpleNamespace

from dash import no_update

from mat_viewer.app import ViewerBackend, callbacks_state


class _CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


def test_state_poll_does_not_cross_contaminate_scene_controls(tmp_path, monkeypatch):
    """A stale pending snapshot may not overwrite the selected scene.

    Creating the second scene arms a pending snapshot for that scene.  We
    then select the first tab without broadcasting a second snapshot, which
    models a tab switch racing with the poll callback.  The callback may
    either ignore the stale event or emit the selected scene's state, but it
    must not return controls/state for the other scene.
    """
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        first_id = backend.active_scene_id()
        second = backend.create_scene(
            structure=backend.get_state()["structure"], label="Second scene"
        )
        second_id = second["id"]
        # ``create_scene`` leaves a pending snapshot for ``second_id``.
        assert backend.pending_state["scene_id"] == second_id

        backend.set_active_scene(first_id, broadcast=False)
        first_state = backend.get_state(first_id)

        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(
                triggered=[{"prop_id": "agent-state-poll.n_intervals"}]
            ),
        )

        result = registry.callbacks["sync_agent_state"](
            1, None, first_id
        )

        # The first output is the selected scene's label.  The penultimate
        # output is agent-state-store.data (camera-store is the last output).
        assert result[0] is no_update or result[0] == first_state["scene_label"]
        returned_state = result[-3]
        assert returned_state is no_update or returned_state["scene_id"] == first_id
    finally:
        backend.close()


def test_state_poll_drops_older_same_scene_snapshot(tmp_path, monkeypatch):
    """A queued pre-edit snapshot must not roll back the selected scene."""
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        scene_id = backend.active_scene_id()
        backend.pop_pending_state()
        backend.patch_state({"display_mode": "cluster"}, broadcast=True)
        older = backend.peek_pending_state()
        assert older is not None
        backend.patch_state({"display_mode": "unit_cell"}, broadcast=False)
        # Model a poll that captured the old queue entry before the newer
        # browser edit committed.
        backend.pending_state = older
        assert backend.get_state(scene_id)["display_mode"] == "unit_cell"

        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "agent-state-poll.n_intervals"}]),
        )
        result = registry.callbacks["sync_agent_state"](1, None, scene_id)
        assert result[1] == "unit_cell"
        assert result[-3]["display_mode"] == "unit_cell"
    finally:
        backend.close()


def test_tab_switch_does_not_requeue_its_own_pending_state(tmp_path, monkeypatch):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        first_id = backend.active_scene_id()
        second = backend.create_scene(
            structure=backend.get_state()["structure"], label="Second scene"
        )
        second_id = second["id"]
        backend.pop_pending_state()

        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "scene-tabs.value"}]),
        )
        result = registry.callbacks["sync_agent_state"](1, None, second_id)

        assert result[-3]["scene_id"] == second_id
        assert backend.pending_state is None
        assert backend.active_scene_id() == second_id
        assert first_id != second_id
    finally:
        backend.close()


def test_upload_sync_reads_server_active_scene_after_stale_tab(tmp_path, monkeypatch):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        first_id = backend.active_scene_id()
        second = backend.create_scene(
            structure=backend.get_state()["structure"], label="Second scene"
        )
        second_id = second["id"]
        backend.set_active_scene(second_id, broadcast=True)

        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(
                triggered=[{"prop_id": "native-upload-sync.data"}]
            ),
        )
        result = registry.callbacks["sync_agent_state"](1, {"seq": 1}, first_id)

        assert result[-3]["scene_id"] == second_id
        assert backend.pending_state is None
    finally:
        backend.close()


def test_capture_state_ignores_batched_tab_refresh_values(tmp_path, monkeypatch):
    """A tab event batched with control refreshes must not write stale controls."""
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        scene_id = backend.active_scene_id()
        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(
                triggered=[
                    {"prop_id": "display-mode-selector.value"},
                    {"prop_id": "scene-tabs.value"},
                ]
            ),
        )
        result = registry.callbacks["capture_state"](
            scene_id,
            "formula_unit",
            ["axes", "hydrogens"],
            1.0,
            0.15,
            0.35,
            "mesh",
            "ball_stick",
            "opacity",
            "ortep_solid",
            0.14,
            None,
            [],
            [],
            "auto",
            None,
            "viridis",
            "auto",
            None,
            None,
            None,
            "#BDBDBD",
            ["show"],
        )
        assert result == (no_update, no_update)
    finally:
        backend.close()

