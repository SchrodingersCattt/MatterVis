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

