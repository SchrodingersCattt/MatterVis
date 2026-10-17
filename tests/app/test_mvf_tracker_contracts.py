"""Acceptance checks for the remaining MVF tracker paths.

These stay on the callback and state APIs. They do not reload a page or
insert a fresh Plotly figure between the action and the assertion.
"""

from __future__ import annotations

from types import SimpleNamespace

from dash import no_update

from mat_viewer.app import ViewerBackend, callbacks_analysis, callbacks_state, callbacks_view
from mat_viewer.app.rightclick import _dispatch_rightclick_action


class _Registry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


def _backend(tmp_path):
    return ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))


def test_first_confirmed_edit_is_published(tmp_path):
    backend = _backend(tmp_path)
    try:
        registry = _Registry()
        callbacks_view.register_view_callbacks(registry, backend)
        state = backend.get_state()
        backend._last_update_kind_by_scene[str(state["scene_id"])] = "display"
        published = registry.callbacks["publish_confirmed_state"](state)
        assert published["update_kind"] == "display"
        assert published["scene_id"] == state["scene_id"]
    finally:
        backend.close()


def test_bfdh_morphology_notifies_the_viewer(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    try:
        calls = []
        monkeypatch.setattr(
            backend._render_worker,
            "request_figure_build",
            lambda state: calls.append(state["scene_id"]) or True,
        )
        monkeypatch.setattr(
            backend,
            "run_bfdh_analysis",
            lambda **kwargs: {
                "status": "ok",
                "warnings": [],
                "facets": [{
                    "miller_index": [1, 0, 0],
                    "d_hkl": 1.0,
                    "relative_morphological_importance": 1.0,
                    "multiplicity": 1,
                }],
                "wulff_facets": [{"hkl": [1, 0, 0]}],
            },
        )
        monkeypatch.setattr(
            callbacks_analysis,
            "callback_context",
            SimpleNamespace(triggered_id="bfdh-run-btn"),
        )
        registry = _Registry()
        callbacks_analysis.register_analysis_callbacks(registry, backend)
        callbacks_view.register_view_callbacks(registry, backend)
        scene_id = backend.active_scene_id()
        registry.callbacks["run_bfdh_analysis"](1, scene_id, 1, 3)
        pending = backend.pending_state
        assert pending and pending["bfdh_morphology"]["enabled"] is True
        registry.callbacks["update_view"](pending, None, None)
        assert calls == [scene_id]
    finally:
        backend.close()


def test_selection_and_vectors_queue_a_complete_frame(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    try:
        calls = []
        monkeypatch.setattr(
            backend._render_worker,
            "request_figure_build",
            lambda state: calls.append(sorted(state)) or True,
        )
        registry = _Registry()
        callbacks_view.register_view_callbacks(registry, backend)
        update = registry.callbacks["update_view"]
        backend.set_selection(["A1"])
        update(backend.get_state(), None, None)
        assert calls and "selection" in calls[-1]
        calls.clear()
        backend.patch_state({
            "vector_overlays": [{
                "id": "g",
                "magnitude_mode": "absolute",
                "arrows": [{"id": "a", "origin": [0, 0, 0], "vector": [1, 0, 0]}],
            }]
        })
        update(backend.get_state(), None, None)
        assert calls and "vector_overlays" in calls[-1]
    finally:
        backend.close()


def test_poll_keeps_another_clients_scene_queued(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    try:
        active = backend.active_scene_id()
        other = backend.create_scene(
            structure=backend.get_state(active)["structure"], label="Other"
        )["id"]
        backend.set_active_scene(active, broadcast=False)
        backend.set_active_scene(other, broadcast=True)
        registry = _Registry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "agent-state-poll.n_intervals"}]),
        )
        result = registry.callbacks["sync_agent_state"](1, None, active)
        assert result[0] is no_update
        assert backend.peek_pending_state()["scene_id"] == other
        assert backend.get_state(active)["scene_id"] == active
    finally:
        backend.close()


def test_click_and_hover_from_another_scene_are_ignored(tmp_path):
    backend = _backend(tmp_path)
    try:
        active = backend.active_scene_id()
        registry = _Registry()
        callbacks_state.register_state_callbacks(registry, backend)
        figure = {"layout": {"meta": {"mattervis_render": {"scene_id": "scene-from-click"}}}}
        result = registry.callbacks["click_to_select_fragment"](
            {"points": [{"pointNumber": 0}]}, active, "asymmetric_unit", [], figure
        )
        assert result is no_update
        before = backend.get_selection(active)
        _dispatch_rightclick_action(
            backend,
            active,
            "select",
            "atom",
            {"label": "A1", "scene_id": "scene-hovered"},
            {"scene_id": "scene-hovered"},
        )
        assert backend.get_selection(active) == before
    finally:
        backend.close()
