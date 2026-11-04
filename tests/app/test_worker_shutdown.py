"""Shutdown must return while a worker is blocked and must not hold the backend lock across disk IO."""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mat_viewer.app.backend import ViewerBackend
from mat_viewer.app.render_worker import _detach_unfinished_workers
from mat_viewer.scenes import core as scene_core
from mat_viewer.scenes.core import SceneStore, SceneStorePersistError

_CHILD_TIMEOUT_SECONDS = 20.0


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
    assert backend.workers_quiescent is False
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


def _run_child(function_name: str, *args: str) -> subprocess.CompletedProcess[str]:
    code = (
        "from tests.app.test_worker_shutdown import "
        f"{function_name}; {function_name}()"
    )
    env = {**os.environ, "MATTERVIS_PREWARM": "0"}
    process = subprocess.Popen(
        [sys.executable, "-c", code, *args],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = process.communicate(timeout=_CHILD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(
            "child did not exit within "
            f"{_CHILD_TIMEOUT_SECONDS:.0f}s\n{stdout}\n{stderr}"
        )
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def child_blocked_worker() -> None:
    """Process entry: close while a finalize worker stays blocked, then exit."""
    root = sys.argv[1]
    report_path = Path(sys.argv[2])
    backend = ViewerBackend(preset_path=str(Path(root) / "preset.json"), root_dir=root)
    started = threading.Event()

    def block() -> None:
        started.set()
        threading.Event().wait()

    backend._render_worker._finalize_pool.submit(block)
    if not started.wait(timeout=10):
        raise SystemExit("blocked worker did not start")
    backend.close()
    report_path.write_text(
        json.dumps({
            "close_returned": True,
            "workers_quiescent": backend.workers_quiescent,
        }),
        encoding="utf-8",
    )


def child_blocked_lock_exit() -> None:
    """Process entry: final flush cannot take the lock, then exit immediately."""
    root = Path(sys.argv[1])
    report_path = Path(sys.argv[2])
    backend = ViewerBackend(preset_path=str(root / "preset.json"), root_dir=str(root))
    held = threading.Event()

    def hold() -> None:
        backend._lock.acquire()
        held.set()
        threading.Event().wait()

    threading.Thread(target=hold, name="hold-backend-lock", daemon=True).start()
    if not held.wait(timeout=5):
        raise SystemExit("backend lock was not held")
    scene = backend.scene_store.add(
        label="unflushed",
        structure_name=backend.scene_store.get().structure_name,
        save=False,
    )
    backend.scene_store.mark_dirty()
    outcome = "returned"
    try:
        backend.close()
    except SceneStorePersistError:
        outcome = "SceneStorePersistError"
    except Exception as exc:  # noqa: BLE001 - the parent asserts the type name
        outcome = type(exc).__name__
    report_path.write_text(
        json.dumps({
            "outcome": outcome,
            "dirty": backend.scene_store.is_dirty(),
            "scene_id": scene.id,
        }),
        encoding="utf-8",
    )
    os._exit(0)


def test_blocked_worker_process_exits_and_reports_non_quiescence(tmp_path: Path):
    report_path = tmp_path / "report.json"
    completed = _run_child("child_blocked_worker", str(tmp_path), str(report_path))
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["close_returned"] is True
    assert report["workers_quiescent"] is False


class _RunningWorker:
    """Stand-in for a pool thread that has already started."""

    def __init__(self) -> None:
        self._tstate_lock = threading.Lock()
        self._daemonic = False
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    @property
    def daemon(self) -> bool:
        return self._daemonic

    @daemon.setter
    def daemon(self, value: bool) -> None:
        raise RuntimeError("cannot set daemon status of active thread")


def test_detach_drops_private_shutdown_registrations():
    from concurrent.futures import thread as futures_thread

    worker = _RunningWorker()
    pool = type("Pool", (), {"_threads": [worker]})()
    threading._shutdown_locks.add(worker._tstate_lock)
    futures_thread._threads_queues[worker] = object()
    try:
        assert _detach_unfinished_workers(pool) is False
        assert worker._tstate_lock not in threading._shutdown_locks
        assert worker not in futures_thread._threads_queues
        assert worker._daemonic is True
        worker.alive = False
        assert _detach_unfinished_workers(pool) is True
    finally:
        threading._shutdown_locks.discard(worker._tstate_lock)
        futures_thread._threads_queues.pop(worker, None)


def test_detach_stays_non_quiescent_without_private_registries(monkeypatch):
    from concurrent.futures import thread as futures_thread

    worker = _RunningWorker()
    pool = type("Pool", (), {"_threads": [worker]})()
    monkeypatch.delattr(threading, "_shutdown_locks", raising=False)
    monkeypatch.delattr(threading, "_shutdown_locks_lock", raising=False)
    monkeypatch.delattr(futures_thread, "_threads_queues", raising=False)
    monkeypatch.delattr(futures_thread, "_global_shutdown_lock", raising=False)
    assert _detach_unfinished_workers(pool) is False
    assert worker._daemonic is True


def test_blocked_lock_close_fails_before_immediate_exit(tmp_path: Path):
    report_path = tmp_path / "report.json"
    completed = _run_child("child_blocked_lock_exit", str(tmp_path), str(report_path))
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["outcome"] == "SceneStorePersistError"
    assert report["dirty"] is True
    disk = json.loads(
        (tmp_path / ".local" / "crystal_view_scenes.json").read_text(encoding="utf-8")
    )
    disk_ids = [item["id"] for item in disk["scenes"]]
    assert report["scene_id"] not in disk_ids
