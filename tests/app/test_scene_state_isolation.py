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


def test_poll_repairs_a_tab_response_the_browser_did_not_apply(tmp_path, monkeypatch):
    """An overlapping poll must not strand the previous scene's controls."""
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        first_id = backend.active_scene_id()
        second = backend.create_scene(
            structure=backend.get_state()["structure"], label="Second scene"
        )["id"]
        backend.pop_pending_state()
        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(callbacks_state, "callback_context", SimpleNamespace(
            triggered=[{"prop_id": "scene-tabs.value"}]
        ))
        sync = registry.callbacks["sync_agent_state"]
        old_stamp = callbacks_state._scene_controls_stamp(backend.get_state(first_id))
        discarded = sync(None, None, None, second, old_stamp)
        second_stamp = callbacks_state._scene_controls_stamp(backend.get_state(second))
        assert discarded[-4] == second_stamp
        assert backend.pending_state is None

        # Model Dash superseding that response: the server filled its state,
        # but the browser still acknowledges the old scene's controls.
        monkeypatch.setattr(callbacks_state, "callback_context", SimpleNamespace(
            triggered=[{"prop_id": "agent-state-poll.n_intervals"}]
        ))
        repaired = sync(1, None, None, second, old_stamp)
        assert repaired[0] == "Second scene"
        assert repaired[-4] == second_stamp
        assert repaired[-3]["scene_id"] == second
        assert repaired[-2] is no_update  # polling must not echo the camera
        assert all(value is no_update for value in sync(2, None, None, second, second_stamp))

        backend.camera_action("reset", scene_id=second, broadcast=False)
        camera_repaired = sync(3, None, None, second, second_stamp)
        assert camera_repaired[-3]["camera_revision"] > repaired[-3]["camera_revision"]
        assert camera_repaired[-4] != second_stamp
        assert camera_repaired[-2] is no_update
    finally:
        backend.close()


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


def test_tab_switch_drops_the_snapshot_it_already_delivers(tmp_path, monkeypatch):
    """Duplicate's queued snapshot must not survive the tab fill.

    The tab callback returns that scene's controls. A later poll of the same
    snapshot writes the label into the rename box again and replaces a name
    the user has started typing.
    """
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        first_id = backend.active_scene_id()
        second_id = backend.create_scene(
            structure=backend.get_state()["structure"], label="Second scene"
        )["id"]
        assert backend.pending_state["scene_id"] == second_id
        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "scene-tabs.value"}]),
        )
        old_stamp = callbacks_state._scene_controls_stamp(backend.get_state(first_id))
        result = registry.callbacks["sync_agent_state"](
            None, None, None, second_id, old_stamp
        )
        assert result[0] == "Second scene"
        assert backend.pending_state is None

        # A newer snapshot than the one just delivered stays queued so the
        # poll can still publish it.
        renamed = backend.get_state(second_id)
        renamed["display_version"] = int(renamed.get("display_version", 0) or 0) + 1
        renamed["scene_label"] = "Renamed during switch"
        backend.pending_state = renamed
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "scene-tabs.value"}]),
        )
        # Delivering the pre-rename state must not discard the newer queue.
        backend.scene_store.get(second_id).label = "Second scene"
        stale = backend.get_state(second_id)
        callbacks_state._drop_acknowledged_pending(backend, stale)
        assert backend.pending_state["scene_label"] == "Renamed during switch"
    finally:
        backend.close()


def test_poll_does_not_retype_a_label_the_rename_box_already_shows(tmp_path, monkeypatch):
    """Repeating the current label must not overwrite an in-progress rename."""
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        scene_id = backend.active_scene_id()
        label = backend.get_state(scene_id)["scene_label"]
        backend.pop_pending_state()
        backend.patch_state({"axis_scale": 1.5}, broadcast=True)
        stamp = callbacks_state._scene_controls_stamp(backend.get_state(scene_id))
        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "agent-state-poll.n_intervals"}]),
        )
        sync = registry.callbacks["sync_agent_state"]
        kept = sync(1, None, None, scene_id, stamp, label)
        assert kept[0] is no_update
        assert kept[-3]["axis_scale"] == 1.5
        assert kept[-2] is no_update

        backend.patch_state({"axis_scale": 1.25}, broadcast=True)
        repaired = sync(2, None, None, scene_id, stamp, "previous label")
        assert repaired[0] == label
    finally:
        backend.close()

