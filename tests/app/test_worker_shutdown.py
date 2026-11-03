"""Shutdown must return while a worker is blocked and must not hold the backend lock across disk IO."""

from __future__ import annotations

import atexit
import json
import threading
import time
from pathlib import Path

import pytest

from mat_viewer.app.backend import ViewerBackend
from mat_viewer.scenes import core as scene_core
from mat_viewer.scenes.core import SceneStore


def _backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def test_flush_does_not_hold_the_backend_lock_during_the_write(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    probed: dict[str, bool] = {}
    original = scene_core.json.dump

    def dumping(payload, handle, **kwargs):
        found: dict[str, bool] = {}

        def probe() -> None:
            acquired = backend._lock.acquire(blocking=False)
            found["free"] = acquired
            if acquired:
                backend._lock.release()

        watcher = threading.Thread(target=probe)
        watcher.start()
        watcher.join()
        probed.update(found)
        original(payload, handle, **kwargs)

    monkeypatch.setattr(scene_core.json, "dump", dumping)
    try:
        backend.scene_store.mark_dirty()
        backend.flush_scene_store()
        assert probed["free"] is True
    finally:
        backend.close()


def test_flush_persists_a_mutation_that_lands_during_the_write(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    extra: dict[str, str] = {}
    original = scene_core.json.dump
    calls = {"n": 0}

    def dumping(payload, handle, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            scene = backend.scene_store.add(
                label="during-write",
                structure_name=backend.scene_store.get().structure_name,
                save=False,
            )
            backend.scene_store.mark_dirty()
            extra["id"] = scene.id
        original(payload, handle, **kwargs)

    monkeypatch.setattr(scene_core.json, "dump", dumping)
    try:
        backend.scene_store.mark_dirty()
        backend.flush_scene_store()
        assert extra["id"] in SceneStore.load(backend.scene_store.path).order
        assert backend.scene_store.is_dirty() is False
    finally:
        backend.close()


def test_close_returns_while_a_finalize_job_is_blocked(tmp_path: Path):
    backend = _backend(tmp_path)
    path = Path(backend.scene_store.path)
    committed_ids = [item["id"] for item in json.loads(path.read_text(encoding="utf-8"))["scenes"]]
    assert committed_ids
    started = threading.Event()
    release = threading.Event()

    def block() -> None:
        started.set()
        release.wait(timeout=30)

    future = backend._render_worker._finalize_pool.submit(block)
    assert started.wait(timeout=2)
    holder = threading.Thread(target=backend.close)
    started_at = time.monotonic()
    holder.start()
    holder.join(12)
    alive = holder.is_alive()
    elapsed = time.monotonic() - started_at
    release.set()
    future.result(timeout=5)
    if alive:
        holder.join(10)
    assert not alive
    assert elapsed < 12
    reloaded = [item["id"] for item in json.loads(path.read_text(encoding="utf-8"))["scenes"]]
    assert reloaded == committed_ids
    assert backend._closed is True


def test_close_flushes_and_unregisters_when_shutdown_fails(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    scene = backend.scene_store.add(
        label="kept",
        structure_name=backend.scene_store.get().structure_name,
        save=False,
    )
    backend.scene_store.mark_dirty()
    original_shutdown = backend._render_worker.shutdown

    def boom(*, wait: bool = True) -> None:
        raise RuntimeError("shutdown failed")

    monkeypatch.setattr(backend._render_worker, "shutdown", boom)
    unregistered: list[object] = []
    original_unregister = atexit.unregister

    def track(callback) -> None:
        unregistered.append(callback)
        original_unregister(callback)

    monkeypatch.setattr(atexit, "unregister", track)
    try:
        with pytest.raises(RuntimeError, match="shutdown failed"):
            backend.close()
        assert backend._atexit_flush_callback in unregistered
        assert scene.id in SceneStore.load(backend.scene_store.path).order
        assert not backend._persist_thread.is_alive()
    finally:
        monkeypatch.setattr(backend._render_worker, "shutdown", original_shutdown)
        original_shutdown(wait=False)
