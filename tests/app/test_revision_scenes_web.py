"""Real Web routing, close/Undo and stale revision focus regression tests."""
from pathlib import Path
from threading import Event

import pytest
from dash import no_update

from mat_viewer.app import create_app
from mat_viewer.extensions import RevisionId, RevisionUnavailable

CIF = Path(__file__).parents[1] / "fixtures" / "cod_1000043.cif"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    app = create_app(root_dir=str(tmp_path), preset_path=str(tmp_path / "preset.json"))
    yield app
    app.close_extensions()
    app.crystal_backend.close()


def callback(app, name):
    return next(v["callback"].__wrapped__ for v in app.callback_map.values()
                if v.get("callback") and v["callback"].__name__ == name)


def rev(token):
    return RevisionId("test.workflow", token)


def test_native_browser_highlights_are_targeted_and_not_feedback(app):
    api = app.extension_context.revisions
    host = app.crystal_backend._extension_structures
    first = api.register(rev("first"), CIF).result(30)
    second = api.register(rev("second"), CIF).result(30)
    a, b = host.new_target(), host.new_target()
    events = []
    api.subscribe(events.append)
    observe = callback(app, "extension_scene_activated")
    observe(first.scene.scene_id, a)
    observe(second.scene.scene_id, b)
    observe(first.scene.scene_id, a)
    assert [(e.target, e.bindings) for e in events] == [(a, (first,)), (b, (second,))]
    assert host.poll(a) is None and host.poll(b) is None
    api.focus(first.revision, target=a).result(30)
    assert host.poll(b) is None
    payload = host.poll(a)
    assert callback(app, "manage_scene_tabs_dom")(payload, None, second.scene.scene_id)[2] == first.scene.scene_id
    # Observing the resulting tab value does not enqueue another focus.
    observe(first.scene.scene_id, a)
    assert host.poll(a) is None
    with pytest.raises(ValueError, match="target"):
        api.focus(first.revision).result(30)


def test_reload_invalidates_already_polled_revision_focus(app):
    api = app.extension_context.revisions
    host = app.crystal_backend._extension_structures
    first = api.register(rev("first"), CIF).result(30)
    target = host.new_target()
    api.focus(first.revision, target=target).result(30)
    stale = host.poll(target)
    second = api.reload(rev("second"), first.scene, CIF).result(30)
    assert callback(app, "manage_scene_tabs_dom")(stale, None, first.scene.scene_id) == (no_update,) * 3
    with pytest.raises(RevisionUnavailable):
        api.focus(first.revision, target=target)
    assert api.lookup(second.revision) == second


def test_close_undo_during_reload_never_restores_binding(app, monkeypatch):
    api = app.extension_context.revisions
    backend = app.crystal_backend
    first = api.register(rev("first"), CIF).result(30)
    host = backend._extension_structures
    entered, release = Event(), Event()
    prepare = host.prepare

    def delayed(*args):
        entered.set()
        assert release.wait(20)
        return prepare(*args)

    monkeypatch.setattr(host, "prepare", delayed)
    pending = api.reload(rev("second"), first.scene, CIF)
    assert entered.wait(5)
    backend.delete_scene(first.scene.scene_id)
    backend.undo_scene_close()
    release.set()
    with pytest.raises(KeyError):
        pending.result(30)
    for key in ("first", "second"):
        with pytest.raises(RevisionUnavailable):
            api.lookup(rev(key))
    assert host.handle(first.scene.scene_id) != first.scene


def test_two_real_hosts_have_independent_mapping_and_routing(app, tmp_path):
    other = create_app(root_dir=str(tmp_path / "other"), preset_path=str(tmp_path / "other.json"))
    try:
        a = app.extension_context.revisions.register(rev("same"), CIF).result(30)
        b = other.extension_context.revisions.register(rev("same"), CIF).result(30)
        assert a.scene != b.scene
        target = app.crystal_backend._extension_structures.new_target()
        with pytest.raises(ValueError, match="target"):
            other.extension_context.revisions.focus(b.revision, target=target).result(30)
        app.crystal_backend.delete_scene(a.scene.scene_id)
        assert other.extension_context.revisions.lookup(b.revision) == b
    finally:
        other.close_extensions()
        other.crystal_backend.close()
