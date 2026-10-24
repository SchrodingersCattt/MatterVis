"""Real Web host writes, browser routing, source ownership and lifecycle races."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, get_ident

import pytest

from mat_viewer.app import create_app

CIF = Path(__file__).parents[1] / "fixtures" / "cod_1000043.cif"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    result = create_app(
        root_dir=str(tmp_path), preset_path=str(tmp_path / "preset.json")
    )
    yield result
    result.close_extensions()
    result.crystal_backend.close()


def dispatcher(app):
    for value in app.callback_map.values():
        callback = value.get("callback")
        if callback and callback.__name__ == "manage_scene_tabs_dom":
            return callback.__wrapped__
    raise AssertionError("missing native tab dispatcher")


def test_register_reload_owns_sources_and_isolates_siblings(app, tmp_path):
    api, backend = app.extension_context.structures, app.crystal_backend
    events = []
    api.subscribe(events.append)
    path = tmp_path / "input.cif"
    path.write_bytes(CIF.read_bytes())
    future = api.register(path, label="Uploaded")
    path.write_text("caller has changed the file")
    scene_id = future.result(timeout=30)
    original = backend.scene_store.get(scene_id).structure_name
    source = Path(backend.bundles[original].cif_path)
    assert source.read_bytes() == CIF.read_bytes()
    sibling = backend.duplicate_scene(scene_id)["id"]
    backend.patch_state(
        {
            "selection": {"targets": [{"kind": "atom", "id": "atom:0"}]},
            "transforms": [{"kind": "repeat", "repeat": [2, 1, 1]}],
        },
        scene_id=scene_id,
    )
    before_callbacks = dict(app.callback_map)
    assert api.reload(scene_id, CIF.read_bytes(), format="cif").result(30) == scene_id
    assert backend.scene_store.get(scene_id).structure_name != original
    assert backend.scene_store.get(sibling).structure_name == original
    assert backend.bundles[original].cif_path == str(source)
    state = backend.get_state(scene_id)
    assert not state["selection"].get("targets")
    assert not state.get("transforms")
    assert state["target_revision"] == 1
    assert app.callback_map == before_callbacks
    assert [e.kind for e in events] == ["registered", "reloaded"]


def test_focus_routes_one_browser_and_never_revives_closed_generation(app):
    api, host = (
        app.extension_context.structures,
        app.crystal_backend._extension_structures,
    )
    backend = app.crystal_backend
    a, b = host.new_target(), host.new_target()
    old_active = backend.active_scene_id()
    scene_id = api.register(CIF).result(30)
    refresh_b = host.poll(b)
    assert dispatcher(app)(refresh_b, None, old_active)[2] == old_active
    api.focus(scene_id, target=a).result(30)
    focus_a = host.poll(a)
    assert dispatcher(app)(focus_a, None, old_active)[2] == scene_id
    assert host.poll(b) is None
    assert backend.active_scene_id() == old_active
    handle = api.handle(scene_id)
    backend.delete_scene(scene_id)
    backend.undo_scene_close()
    assert api.handle(scene_id) != handle
    assert dispatcher(app)(focus_a, None, old_active)[2] == old_active
    with pytest.raises(KeyError):
        api.focus(handle, target=a).result(30)
    with pytest.raises(ValueError, match="target"):
        api.focus(scene_id).result(30)


def test_worker_reload_close_race_is_atomic(app, monkeypatch):
    api, backend = app.extension_context.structures, app.crystal_backend
    scene_id = api.register(CIF).result(30)
    original_bundles = set(backend.bundles)
    entered, release = Event(), Event()
    prepare = api.host.prepare
    worker_threads = []

    def delayed(*args):
        worker_threads.append(get_ident())
        entered.set()
        assert release.wait(20)
        return prepare(*args)

    monkeypatch.setattr(api.host, "prepare", delayed)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(api.reload, scene_id, CIF).result(5)
    assert entered.wait(5)
    backend.delete_scene(scene_id)
    backend.undo_scene_close()
    release.set()
    with pytest.raises(KeyError):
        pending.result(30)
    assert set(backend.bundles) == original_bundles
    assert worker_threads[0] != get_ident()


def test_invalid_inputs_and_parse_failure_leave_host_unchanged(app):
    api, backend = app.extension_context.structures, app.crystal_backend
    before = backend.scene_options()
    bundles = set(backend.bundles)
    for source, kwargs in [
        (b"x", {}),
        (b"", {"format": "cif"}),
        (b"x", {"format": "xyz"}),
        (CIF, {"label": ""}),
    ]:
        with pytest.raises(ValueError):
            api.register(source, **kwargs)
    with pytest.raises(Exception):
        api.register(b"not a structure", format="cif").result(30)
    assert backend.scene_options() == before
    assert set(backend.bundles) == bundles
    assert not list(Path(api._storage.name).iterdir())


def test_failed_host_commit_rolls_back_reload(app, monkeypatch):
    api, backend = app.extension_context.structures, app.crystal_backend
    scene_id = api.register(CIF).result(30)
    state = backend.get_state(scene_id)
    bundles = set(backend.bundles)
    original = backend.default_state

    def fail_candidate(name, **kwargs):
        if name not in bundles:
            raise ValueError("invalid candidate defaults")
        return original(name, **kwargs)

    monkeypatch.setattr(backend, "default_state", fail_candidate)
    with pytest.raises(ValueError, match="candidate"):
        api.reload(scene_id, CIF).result(30)
    assert backend.get_state(scene_id) == state
    assert set(backend.bundles) == bundles


def test_focus_waiting_behind_parse_rejects_closed_scene(app, monkeypatch):
    api, backend = app.extension_context.structures, app.crystal_backend
    scene_id = api.register(CIF).result(30)
    entered, release = Event(), Event()
    prepare = api.host.prepare

    def delayed(*args):
        entered.set()
        assert release.wait(20)
        return prepare(*args)

    monkeypatch.setattr(api.host, "prepare", delayed)
    pending = api.register(CIF)
    assert entered.wait(5)
    focus = api.focus(scene_id, target=api.host.new_target())
    backend.delete_scene(scene_id)
    release.set()
    pending.result(30)
    with pytest.raises(KeyError):
        focus.result(30)


def test_identical_geometry_preserves_camera_and_shutdown_rejects_writes(app):
    api, backend = app.extension_context.structures, app.crystal_backend
    scene_id = api.register(CIF).result(30)
    camera = {
        "eye": {"x": 2.5, "y": 1.5, "z": 2.0},
        "up": {"x": 0, "y": 0, "z": 1},
        "center": {"x": 0, "y": 0, "z": 0},
    }
    backend.patch_state({"camera": camera}, scene_id=scene_id)
    before = backend.get_state(scene_id)["camera"]
    api.reload(scene_id, CIF).result(30)
    assert backend.get_state(scene_id)["camera"] == before
    api.close()
    with pytest.raises(RuntimeError, match="closed"):
        api.register(CIF)


def test_later_focus_supersedes_already_polled_event(app):
    from dash import no_update

    api = app.extension_context.structures
    a = api.register(CIF).result(30)
    b = api.register(CIF).result(30)
    target = api.host.new_target()
    api.focus(a, target=target).result(30)
    stale_event = api.host.poll(target)
    api.focus(b, target=target).result(30)
    assert dispatcher(app)(stale_event, None, a) == (no_update, no_update, no_update)
    assert dispatcher(app)(api.host.poll(target), None, a)[2] == b


def test_shutdown_during_parse_rejects_commit_and_removes_candidate(app, monkeypatch):
    api, backend = app.extension_context.structures, app.crystal_backend
    before = backend.scene_options()
    entered, release = Event(), Event()
    prepare = api.host.prepare

    def delayed(*args):
        entered.set()
        assert release.wait(20)
        return prepare(*args)

    monkeypatch.setattr(api.host, "prepare", delayed)
    pending = api.register(CIF)
    assert entered.wait(5)
    candidates = list(Path(api._storage.name).iterdir())
    api.close()
    release.set()
    with pytest.raises(RuntimeError, match="closed"):
        pending.result(30)
    assert backend.scene_options() == before
    assert all(not path.exists() for path in candidates)
