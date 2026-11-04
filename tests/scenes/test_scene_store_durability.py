"""Scene-store writes must not drop a mutation that lands during the replace."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from mat_viewer.scenes import SceneStore
from mat_viewer.scenes import core as scene_core


def test_save_keeps_a_mutation_that_lands_during_the_write(tmp_path, monkeypatch):
    store = SceneStore(str(tmp_path / "scenes.json"))
    store.add(label="A", structure_name="DAP-4", save=False)
    store.mark_dirty()
    extra: dict[str, str] = {}
    original = scene_core.json.dump

    def dumping(payload, handle, **kwargs):
        if "id" not in extra:
            scene = store.add(label="B", structure_name="DAP-4", save=False)
            store.mark_dirty()
            extra["id"] = scene.id
        original(payload, handle, **kwargs)

    monkeypatch.setattr(scene_core.json, "dump", dumping)

    store.save()

    assert store.is_dirty()
    assert extra["id"] not in _scene_ids(store.path)
    store.save()
    assert extra["id"] in _scene_ids(store.path)
    assert store.is_dirty() is False


def _scene_ids(path: str) -> list[str]:
    loaded = SceneStore.load(path)
    return list(loaded.order)


def _disk_generation(path: str) -> int:
    return int(json.loads(Path(path).read_text(encoding="utf-8"))["generation"])


def _flush(store: SceneStore, method: str) -> None:
    if method == "save":
        store.save()
        return
    store.flush_durable(threading.RLock())


@pytest.mark.parametrize("method", ["save", "flush"])
def test_overlapping_flushes_never_clean_an_older_disk_generation(tmp_path, monkeypatch, method):
    """Two writers must not publish generation 1 after generation 2 is clean.

    The first writer stalls inside the replace. The second mutates and
    flushes. It must not finish, and must not leave a clean store whose
    file is older than the in-memory generation.
    """
    store = SceneStore(str(tmp_path / "scenes.json"))
    store.add(label="base", structure_name="water", save=False)
    store.mark_dirty()
    first_generation = store._generation
    inside = threading.Event()
    release = threading.Event()
    reached = threading.Event()
    second_done = threading.Event()
    original = scene_core.json.dump
    errors: list[BaseException] = []

    def dumping(payload, handle, **kwargs):
        if payload.get("generation") == first_generation and not inside.is_set():
            inside.set()
            if not release.wait(timeout=5):
                raise TimeoutError("first writer was not released")
        original(payload, handle, **kwargs)

    monkeypatch.setattr(scene_core.json, "dump", dumping)

    def first() -> None:
        try:
            _flush(store, method)
        except BaseException as exc:  # noqa: BLE001 - the thread reports it
            errors.append(exc)

    def second() -> None:
        try:
            assert inside.wait(timeout=5)
            store.add(label="newer", structure_name="water", save=False)
            store.mark_dirty()
            reached.set()
            _flush(store, method)
        except BaseException as exc:  # noqa: BLE001 - the thread reports it
            errors.append(exc)
        finally:
            second_done.set()

    threads = [
        threading.Thread(target=first),
        threading.Thread(target=second),
    ]
    for thread in threads:
        thread.start()
    assert inside.wait(timeout=5)
    assert reached.wait(timeout=5)
    # The second flush is blocked on the store flush lock. This wait is
    # that overlap, not a scene-readiness sleep.
    finished_during_first_write = second_done.wait(timeout=0.5)
    release.set()
    for thread in threads:
        thread.join(5)
    assert errors == []
    assert finished_during_first_write is False
    assert store.is_dirty() is False
    disk_generation = _disk_generation(store.path)
    assert disk_generation == store._generation
    assert disk_generation > first_generation
