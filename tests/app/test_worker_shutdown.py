"""Shutdown must return while a worker is blocked and must not hold the backend lock across disk IO.

Subprocess cases time interpreter startup separately from ``close``.
A hung close still fails the close deadline. A slow import does not.
"""

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

# ``close`` joins a blocked pool for 5s and the persist thread for 2s.
# This is the same bound the in-process test already uses for that
# return. It starts only after the child reports that ``close`` is
# about to run, so interpreter startup is not part of it.
_CLOSE_DEADLINE_SECONDS = 12.0
# Idle import of this module is under 1s. Eight concurrent imports on
# a 4-core host stayed under 3s, and a blocked-worker child under CPU
# contention still exited in about 8s, of which 5s was the pool join.
# The previous 20s limit covered startup and that join together, so a
# slow import was reported as a hung child. 45s is only the startup
# budget. It is not a longer shutdown wait.
_CHILD_STARTUP_TIMEOUT_SECONDS = 45.0
# pytest-timeout in thread mode kills the worker with ``os._exit`` and
# drops this file's phase message. Stay above the sum of the two
# deadlines so that message is the failure the runner records.
_CHILD_PYTEST_TIMEOUT_SECONDS = _CHILD_STARTUP_TIMEOUT_SECONDS + _CLOSE_DEADLINE_SECONDS + 10
_PHASE_POLL_SECONDS = 0.05


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
    holder.join(_CLOSE_DEADLINE_SECONDS)
    alive = holder.is_alive()
    elapsed = time.monotonic() - started_at
    assert backend.workers_quiescent is False
    release.set()
    future.result(timeout=5)
    if alive:
        holder.join(10)
    assert not alive
    assert elapsed < _CLOSE_DEADLINE_SECONDS
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


class _ChildWatch:
    """Result of waiting on a child with separate startup and close clocks."""

    def __init__(
        self,
        completed: subprocess.CompletedProcess[str],
        failure: str | None,
        startup_s: float,
        shutdown_s: float | None,
        phase: str,
    ) -> None:
        self.completed = completed
        self.failure = failure
        self.startup_s = startup_s
        self.shutdown_s = shutdown_s
        self.phase = phase


def _phase_path(report_path: Path) -> Path:
    return Path(str(report_path) + ".phase")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _publish_phase(report_path: Path, phase: str, **fields: object) -> None:
    _write_json(_phase_path(report_path), {"phase": phase, **fields})


def _read_stream(stream: object, sink: list[str]) -> None:
    data = stream.read() if hasattr(stream, "read") else ""
    sink.append(data or "")


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.wait()


def _watch_child(
    process: subprocess.Popen[str],
    phase_path: Path,
    *,
    startup_timeout: float,
    shutdown_timeout: float,
) -> _ChildWatch:
    """Wait until the child starts ``close``, then apply the close deadline.

    ``startup_timeout`` covers interpreter startup, imports, and setup.
    ``shutdown_timeout`` starts when the phase file says ``close_starting``.
    A child that never reaches ``close`` is not reported as a shutdown hang.
    """
    stdout_sink: list[str] = []
    stderr_sink: list[str] = []
    readers = [
        threading.Thread(
            target=_read_stream,
            args=(process.stdout, stdout_sink),
            name="mattervis-child-stdout",
            daemon=True,
        ),
        threading.Thread(
            target=_read_stream,
            args=(process.stderr, stderr_sink),
            name="mattervis-child-stderr",
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    spawned = time.monotonic()
    ready_at: float | None = None
    phase_name = ""

    def _note_phase() -> str:
        nonlocal phase_name
        payload = _read_json(phase_path)
        name = payload.get("phase")
        if isinstance(name, str) and name:
            phase_name = name
        return phase_name

    deadline = spawned + startup_timeout
    while ready_at is None and time.monotonic() < deadline:
        if _note_phase() == "close_starting":
            ready_at = time.monotonic()
            break
        if process.poll() is not None:
            break
        time.sleep(_PHASE_POLL_SECONDS)
    if ready_at is None and _note_phase() == "close_starting":
        ready_at = time.monotonic()
    startup_s = (ready_at if ready_at is not None else time.monotonic()) - spawned
    failure: str | None = None
    shutdown_s: float | None = None
    if ready_at is None:
        if process.poll() is None:
            _stop_process(process)
            failure = (
                f"child did not reach close() within {startup_timeout:.0f}s\n"
                f"last_phase={phase_name or 'none'} startup_s={startup_s:.3f}"
            )
        else:
            failure = (
                "child exited before close() with "
                f"returncode {process.returncode} after {startup_s:.3f}s\n"
                f"last_phase={phase_name or 'none'}"
            )
    elif process.poll() is None:
        remaining = shutdown_timeout - (time.monotonic() - ready_at)
        try:
            process.wait(timeout=max(0.0, remaining))
        except subprocess.TimeoutExpired:
            shutdown_s = time.monotonic() - ready_at
            _stop_process(process)
            failure = (
                "child did not exit within "
                f"{shutdown_timeout:.0f}s after close() started\n"
                f"startup_s={startup_s:.3f} shutdown_s={shutdown_s:.3f} "
                f"last_phase={phase_name or 'close_starting'}"
            )
    if ready_at is not None and failure is None:
        shutdown_s = time.monotonic() - ready_at
    for reader in readers:
        reader.join(timeout=5)
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()
    stdout = "".join(stdout_sink)
    stderr = "".join(stderr_sink)
    if failure:
        if stdout:
            failure = f"{failure}\n{stdout}"
        if stderr:
            failure = f"{failure}\n{stderr}"
    returncode = process.returncode if process.returncode is not None else 1
    completed = subprocess.CompletedProcess(
        process.args,
        returncode,
        stdout,
        stderr,
    )
    return _ChildWatch(completed, failure, startup_s, shutdown_s, phase_name)


def _run_child(
    function_name: str,
    *args: str,
    startup_timeout: float = _CHILD_STARTUP_TIMEOUT_SECONDS,
    shutdown_timeout: float = _CLOSE_DEADLINE_SECONDS,
) -> subprocess.CompletedProcess[str]:
    code = (
        "from tests.app.test_worker_shutdown import "
        f"{function_name}; {function_name}()"
    )
    env = {**os.environ, "MATTERVIS_PREWARM": "0"}
    report_path = Path(args[1])
    process = subprocess.Popen(
        [sys.executable, "-c", code, *args],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    watched = _watch_child(
        process,
        _phase_path(report_path),
        startup_timeout=startup_timeout,
        shutdown_timeout=shutdown_timeout,
    )
    if watched.failure:
        pytest.fail(watched.failure)
    return watched.completed


def child_blocked_worker() -> None:
    """Process entry: close while a finalize worker stays blocked, then exit."""
    entered = time.perf_counter()
    root = sys.argv[1]
    report_path = Path(sys.argv[2])
    _publish_phase(report_path, "imported", since_enter_s=0.0)
    backend = ViewerBackend(preset_path=str(Path(root) / "preset.json"), root_dir=root)
    started = threading.Event()

    def block() -> None:
        started.set()
        threading.Event().wait()

    backend._render_worker._finalize_pool.submit(block)
    if not started.wait(timeout=10):
        raise SystemExit("blocked worker did not start")
    _publish_phase(
        report_path,
        "close_starting",
        since_enter_s=round(time.perf_counter() - entered, 4),
    )
    close_started = time.perf_counter()
    backend.close()
    report_path.write_text(
        json.dumps({
            "close_returned": True,
            "workers_quiescent": backend.workers_quiescent,
            "close_s": round(time.perf_counter() - close_started, 4),
        }),
        encoding="utf-8",
    )


def child_blocked_lock_exit() -> None:
    """Process entry: final flush cannot take the lock, then exit immediately."""
    entered = time.perf_counter()
    root = Path(sys.argv[1])
    report_path = Path(sys.argv[2])
    _publish_phase(report_path, "imported", since_enter_s=0.0)
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
    _publish_phase(
        report_path,
        "close_starting",
        since_enter_s=round(time.perf_counter() - entered, 4),
    )
    outcome = "returned"
    close_started = time.perf_counter()
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
            "close_s": round(time.perf_counter() - close_started, 4),
        }),
        encoding="utf-8",
    )
    os._exit(0)


@pytest.mark.timeout(_CHILD_PYTEST_TIMEOUT_SECONDS)
def test_blocked_worker_process_exits_and_reports_non_quiescence(tmp_path: Path):
    report_path = tmp_path / "report.json"
    completed = _run_child("child_blocked_worker", str(tmp_path), str(report_path))
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["close_returned"] is True
    assert report["workers_quiescent"] is False
    assert report["close_s"] < _CLOSE_DEADLINE_SECONDS


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


@pytest.mark.timeout(_CHILD_PYTEST_TIMEOUT_SECONDS)
def test_blocked_lock_close_fails_before_immediate_exit(tmp_path: Path):
    report_path = tmp_path / "report.json"
    completed = _run_child("child_blocked_lock_exit", str(tmp_path), str(report_path))
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["outcome"] == "SceneStorePersistError"
    assert report["dirty"] is True
    assert report["close_s"] < _CLOSE_DEADLINE_SECONDS
    disk = json.loads(
        (tmp_path / ".local" / "crystal_view_scenes.json").read_text(encoding="utf-8")
    )
    disk_ids = [item["id"] for item in disk["scenes"]]
    assert report["scene_id"] not in disk_ids


# No ViewerBackend import. These only check which deadline the parent names.
_PHASE_PROBE = """
import json, os, sys, time
from pathlib import Path
report = Path(sys.argv[2])
phase = Path(str(report) + ".phase")
temporary = Path(str(phase) + ".tmp")
temporary.write_text(json.dumps({"phase": sys.argv[1]}), encoding="utf-8")
os.replace(temporary, phase)
time.sleep(30)
"""


def _probe_child(phase: str, report_path: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", _PHASE_PROBE, phase, str(report_path)],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={**os.environ, "MATTERVIS_PREWARM": "0"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_startup_stall_is_not_reported_as_a_shutdown_hang(tmp_path: Path):
    report_path = tmp_path / "report.json"
    process = _probe_child("imported", report_path)
    watched = _watch_child(
        process,
        _phase_path(report_path),
        startup_timeout=0.8,
        shutdown_timeout=0.2,
    )
    assert watched.failure is not None
    assert "did not reach close()" in watched.failure
    assert "after close() started" not in watched.failure
    assert watched.shutdown_s is None
    assert watched.startup_s < 2
    assert process.poll() is not None


def test_close_stall_is_not_reported_as_a_startup_timeout(tmp_path: Path):
    report_path = tmp_path / "report.json"
    process = _probe_child("close_starting", report_path)
    watched = _watch_child(
        process,
        _phase_path(report_path),
        startup_timeout=5,
        shutdown_timeout=0.4,
    )
    assert watched.failure is not None
    assert "after close() started" in watched.failure
    assert "did not reach close()" not in watched.failure
    assert watched.shutdown_s is not None
    assert watched.shutdown_s < 2
    assert process.poll() is not None
