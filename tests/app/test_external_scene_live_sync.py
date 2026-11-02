"""API scene writes reach an open viewer without a Dash ``update_view`` call.

The commit point schedules complete frames. Scene-list messages reuse the
figure journal, and the existing tab callback keeps the selected scene unless
focus was requested or that scene was removed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from dash import no_update

from mat_viewer.api.ws import handle_ws_message
from mat_viewer.app import ViewerBackend, callbacks_state, create_app
from mat_viewer.app.scene_controls import _scene_control_values, _scene_controls_stamp


class _CallbackRegistry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register


@pytest.fixture
def backend(tmp_path, monkeypatch):
    viewer = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )
    requested = []

    def _request(state, force=False):
        requested.append({"state": state, "force": force})
        return True

    monkeypatch.setattr(viewer._render_worker, "request_figure_build", _request)
    viewer._requested_frames = requested
    try:
        yield viewer
    finally:
        viewer.close()


def _callbacks(backend):
    registry = _CallbackRegistry()
    callbacks_state.register_state_callbacks(registry, backend)
    return registry.callbacks


def _catalog_event(backend, *, focus=False):
    return {
        "source": "scene_catalog",
        "catalog_seq": backend.scene_catalog_seq(),
        "focus": focus,
    }


def test_state_patch_schedules_complete_frame_without_update_view(backend):
    scene_id = backend.active_scene_id()
    before = backend.get_state(scene_id)
    backend._requested_frames.clear()

    backend.patch_state(
        {"display_mode": "unit_cell", "atom_scale": before["atom_scale"] + 0.25},
        scene_id=scene_id,
    )

    assert backend._requested_frames
    scheduled = backend._requested_frames[-1]["state"]
    assert scheduled["scene_id"] == scene_id
    assert scheduled["display_mode"] == "unit_cell"
    assert scheduled["atom_scale"] == pytest.approx(before["atom_scale"] + 0.25)


def test_lightweight_patch_stays_on_the_view_journal(backend):
    scene_id = backend.active_scene_id()
    before = backend.get_state(scene_id)
    cursor = backend.latest_figure_seq()
    backend._requested_frames.clear()

    backend.patch_state({"axis_scale": before["axis_scale"] + 0.05}, scene_id=scene_id)

    assert backend._requested_frames == []
    updates = [
        item
        for item in backend.figure_broadcasts_since(cursor)
        if item.get("type") == "view_update"
    ]
    assert [item["update_kind"] for item in updates] == ["overlay"]


def test_external_intent_broadcasts_and_gui_intent_does_not(backend):
    scene_id = backend.active_scene_id()
    original = list(backend.get_state(scene_id).get("display_options") or [])
    toggled = [item for item in original if item != "labels"]
    if toggled == original:
        toggled = original + ["labels"]
    backend.pop_pending_state()
    cursor = backend.latest_figure_seq()
    backend._requested_frames.clear()

    backend.apply_intent(
        {
            "type": "set_display_options",
            "scene_id": scene_id,
            "payload": {"display_options": toggled},
        }
    )

    assert backend._requested_frames == []
    assert backend.peek_pending_state() is None
    assert [
        item
        for item in backend.figure_broadcasts_since(cursor)
        if item.get("type") == "view_update"
    ] == []
    assert backend.get_state(scene_id)["display_options"] != original

    cursor = backend.latest_figure_seq()
    backend.apply_intent(
        {
            "type": "set_display_options",
            "scene_id": scene_id,
            "payload": {"display_options": original},
        },
        broadcast=True,
    )

    updates = [
        item
        for item in backend.figure_broadcasts_since(cursor)
        if item.get("type") == "view_update"
    ]
    assert [item["update_kind"] for item in updates] == ["display"]
    assert backend.peek_pending_state()["scene_id"] == scene_id
    assert backend.peek_pending_state()["display_options"] == original


def test_external_camera_intent_is_not_suppressed(backend):
    scene_id = backend.active_scene_id()
    camera = dict(backend.get_camera(scene_id) or {})
    eye = dict(camera.get("eye") or {"x": 1, "y": 1, "z": 1})
    eye["x"] = float(eye.get("x", 1)) + 0.2
    camera["eye"] = eye
    backend.pop_pending_state()
    cursor = backend.latest_figure_seq()
    backend._requested_frames.clear()

    hidden = backend.apply_intent(
        {"type": "set_camera", "scene_id": scene_id, "payload": {"camera": camera}}
    )
    assert hidden.get("stale_camera") is not True
    assert [
        item
        for item in backend.figure_broadcasts_since(cursor)
        if item.get("type") == "view_update"
    ] == []

    eye["x"] = float(eye["x"]) + 0.1
    camera = dict(camera)
    camera["eye"] = dict(eye)
    cursor = backend.latest_figure_seq()
    backend.apply_intent(
        {"type": "set_camera", "scene_id": scene_id, "payload": {"camera": camera}},
        broadcast=True,
    )
    updates = [
        item
        for item in backend.figure_broadcasts_since(cursor)
        if item.get("type") == "view_update"
    ]
    assert [item["update_kind"] for item in updates] == ["camera"]
    assert backend._requested_frames == []


def test_inactive_scene_patch_does_not_steal_the_active_scene(backend, monkeypatch):
    active = backend.active_scene_id()
    other = backend.create_scene(
        structure=backend.get_state(active)["structure"],
        label="Other",
    )["id"]
    backend.set_active_scene(active, broadcast=False)
    backend.pop_pending_state()
    backend._requested_frames.clear()
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(triggered_id="scene-event-store", triggered=[]),
    )
    manage = _callbacks(backend)["manage_scene_tabs_dom"]

    backend.patch_state({"display_mode": "cluster", "atom_scale": 0.4}, scene_id=other)

    assert backend.active_scene_id() == active
    assert backend.get_state(active)["display_mode"] != "cluster"
    assert backend.get_state(other)["display_mode"] == "cluster"
    assert backend.peek_pending_state() is None
    assert backend._requested_frames[-1]["state"]["scene_id"] == other
    tabs, _row, value = manage(_catalog_event(backend), None, active)
    assert value is no_update
    assert len(tabs) == 2


def test_scene_catalog_reconciles_tabs_without_stealing_focus(backend, monkeypatch):
    active = backend.active_scene_id()
    created = backend.create_scene(
        structure=backend.get_state(active)["structure"],
        label="Created",
    )
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(triggered_id="scene-event-store", triggered=[]),
    )
    manage = _callbacks(backend)["manage_scene_tabs_dom"]
    tabs, _row, value = manage(_catalog_event(backend), None, active)
    assert value is no_update
    assert len(tabs) == 2

    renamed = "Created rename"
    backend.update_scene(created["id"], {"label": renamed})
    tabs, _row, value = manage(_catalog_event(backend), None, active)
    assert value is no_update
    labels = []
    for tab in tabs:
        text = getattr(tab, "label", None)
        label = getattr(text, "children", None)
        if isinstance(label, list) and label:
            labels.append(getattr(label[0], "children", None))
    assert renamed in labels

    stale_seq = backend.scene_catalog_seq()
    backend.delete_scene(created["id"])
    assert manage(
        {"source": "scene_catalog", "catalog_seq": stale_seq, "focus": True},
        None,
        active,
    ) == (no_update, no_update, no_update)
    _tabs, _row, value = manage(_catalog_event(backend), None, created["id"])
    assert value == active

    snapshot = backend.websocket_snapshot()
    assert snapshot["scene_catalog"]["scene_catalog_seq"] == backend.scene_catalog_seq()
    assert snapshot["scene_catalog"]["focus"] is False
    assert any(item["type"] == "scene_catalog" for item in backend.figure_broadcasts_since(0))


def test_explicit_focus_moves_the_selected_tab(backend, monkeypatch):
    active = backend.active_scene_id()
    other = backend.create_scene(
        structure=backend.get_state(active)["structure"],
        label="Focus target",
    )["id"]
    backend.set_active_scene(active, broadcast=False)
    backend.set_active_scene(other, focus_clients=True)
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(triggered_id="scene-event-store", triggered=[]),
    )
    manage = _callbacks(backend)["manage_scene_tabs_dom"]
    _tabs, _row, value = manage(_catalog_event(backend, focus=True), None, active)
    assert value == other
    catalogs = [
        item for item in backend.figure_broadcasts_since(0) if item.get("type") == "scene_catalog"
    ]
    assert catalogs[-1]["focus"] is True
    assert catalogs[-1]["active_id"] == other


def test_selected_scene_poll_follows_an_external_patch_after_create(backend, monkeypatch):
    active = backend.active_scene_id()
    before = backend.get_state(active)
    stamp = _scene_controls_stamp(before, _scene_control_values(before))
    backend.create_scene(structure=before["structure"], label="Side")
    assert backend.active_scene_id() != active
    backend.patch_state({"atom_scale": before["atom_scale"] + 0.3}, scene_id=active)
    registry = _callbacks(backend)
    monkeypatch.setattr(
        callbacks_state,
        "callback_context",
        SimpleNamespace(
            triggered=[{"prop_id": "agent-state-poll.n_intervals"}],
            triggered_id="agent-state-poll",
        ),
    )
    response = registry["sync_agent_state"](1, None, None, active, stamp, before.get("scene_label"))
    assert response[3] == pytest.approx(before["atom_scale"] + 0.3)
    assert response[1] == before["display_mode"]


def test_http_journal_carries_the_scene_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    requested = []

    def _request(*args, **kwargs):
        requested.append(args)
        return True

    monkeypatch.setattr(app.crystal_backend._render_worker, "request_figure_build", _request)
    try:
        client = app.server.test_client()
        created = client.post(
            "/api/v2/scenes",
            json={"label": "HTTP scene"},
        ).get_json()
        response = client.post(
            "/api/v2/state",
            json={"atom_scale": 0.77, "scene_id": created["id"]},
        )
        assert response.status_code == 200
        assert response.get_json()["atom_scale"] == pytest.approx(0.77)
        body = client.get("/api/v2/view-updates?since=0").get_json()
        assert any(item["id"] == created["id"] for item in body["scene_catalog"]["scenes"])
        assert requested
        assert requested[-1][0]["scene_id"] == created["id"]
        focused = client.post(
            "/api/v2/scenes/active",
            json={"scene_id": created["id"]},
        )
        assert focused.status_code == 200
        catalogs = [
            item
            for item in client.get("/api/v2/view-updates?since=0").get_json()["updates"]
            if item.get("type") == "scene_catalog"
        ]
        assert catalogs[-1]["focus"] is True
        assert catalogs[-1]["active_id"] == created["id"]
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_websocket_intent_requests_broadcast(backend, monkeypatch):
    seen = {}

    def spy(payload, broadcast=None):
        seen["broadcast"] = broadcast
        seen["type"] = payload.get("type")
        return {"ok": True}

    monkeypatch.setattr(backend, "apply_intent", spy)
    handle_ws_message(
        backend,
        {"type": "intent", "payload": {"type": "set_display_options", "payload": {"display_options": []}}},
    )
    assert seen == {"broadcast": True, "type": "set_display_options"}
