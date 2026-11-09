"""Cross-scene patches and the figure journal must not deadlock or leak."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from mat_viewer.app import ViewerBackend

# The race below calls the real patch and journal paths. A fixed join is the
# wrong oracle: each read copies the capped journal window, so a busy machine
# can still be making progress when that join expires. Failure is an opposite
# lock wait that stays latched, and the check runs in a child so a stall
# cannot keep the pytest process alive.
_RACE_ITERATIONS = 1000
_LOCK_STALL_SECONDS = 2.0
_LOCK_POLL_SECONDS = 0.05
_HEARTBEAT_SECONDS = 0.25
_RACE_CHILD_TIMEOUT_SECONDS = 40.0
_DETECTOR_SELF_TIMEOUT_SECONDS = 8.0
_OPPOSITE_ORDER_CHILD_TIMEOUT_SECONDS = 12.0


def _backend(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    return backend


class _WaitTrackingLock:
    """Proxy a lock and record threads blocked inside acquire.

    The waiter set is updated before the inner acquire and cleared when it
    returns, including when a timeout expires. A lock-order cycle leaves the
    two racers waiting on opposite locks.
    """

    def __init__(self, inner: threading.Lock | threading.RLock) -> None:
        self._inner = inner
        self._meta = threading.Lock()
        self._waiters: set[int] = set()

    def waiters(self) -> set[int]:
        with self._meta:
            return set(self._waiters)

    def acquire(self, blocking: bool = True, timeout: float = -1):
        ident = threading.get_ident()
        if blocking:
            with self._meta:
                self._waiters.add(ident)
        try:
            if timeout == -1:
                return self._inner.acquire(blocking)
            return self._inner.acquire(blocking, timeout)
        finally:
            if blocking:
                with self._meta:
                    self._waiters.discard(ident)

    def release(self) -> None:
        self._inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


def _write_report(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _load_report(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"status": "unreadable"}
    return loaded if isinstance(loaded, dict) else {"status": "unreadable"}


def _opposite_wait(
    backend_lock: _WaitTrackingLock,
    journal_lock: _WaitTrackingLock,
    writer_id: int | None,
    reader_id: int | None,
) -> str:
    if writer_id is None or reader_id is None:
        return ""
    backend_waiters = backend_lock.waiters()
    journal_waiters = journal_lock.waiters()
    if writer_id in journal_waiters and reader_id in backend_waiters:
        return "writer waited on the journal lock while reader waited on the backend lock"
    if writer_id in backend_waiters and reader_id in journal_waiters:
        return "writer waited on the backend lock while reader waited on the journal lock"
    return ""


def _watch_opposite_wait(
    backend_lock: _WaitTrackingLock,
    journal_lock: _WaitTrackingLock,
    writer: threading.Thread,
    reader: threading.Thread,
    started: float,
    *,
    on_sample,
    self_timeout: float | None,
) -> tuple[str, str]:
    stall_since: float | None = None
    while True:
        now = time.monotonic()
        both_alive = writer.is_alive() and reader.is_alive()
        detail = ""
        if both_alive:
            detail = _opposite_wait(backend_lock, journal_lock, writer.ident, reader.ident)
        if detail:
            if stall_since is None:
                stall_since = now
            elif now - stall_since >= _LOCK_STALL_SECONDS:
                return "deadlock", detail
        else:
            stall_since = None
        on_sample(detail)
        if not writer.is_alive() and not reader.is_alive():
            return "finished", ""
        if self_timeout is not None and now - started >= self_timeout:
            return "error", "lock-order watchdog did not report a stall"
        time.sleep(_LOCK_POLL_SECONDS)


def _run_child(
    function_name: str,
    *args: str,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    code = (
        "from tests.app.test_scene_isolation_and_journal import "
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
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _format_race_failure(
    completed: subprocess.CompletedProcess[str],
    report: dict[str, object],
) -> str:
    status = report.get("status", "missing")
    detail = str(report.get("detail") or "")
    lines = [
        f"journal race failed: status={status} returncode={completed.returncode}",
        (
            "writer_iterations="
            f"{report.get('writer_iterations')} reader_iterations="
            f"{report.get('reader_iterations')} elapsed_s={report.get('elapsed_s')}"
        ),
    ]
    if status == "deadlock":
        lines.append(f"opposite lock order: {detail}")
    elif status == "running":
        if detail:
            lines.append(f"opposite lock wait in progress: {detail}")
        else:
            lines.append(
                "child was still making progress and was killed at the hard cap; "
                "this is not a lock-order deadlock"
            )
    elif detail:
        lines.append(detail)
    errors = report.get("errors") or []
    if isinstance(errors, list) and errors:
        lines.append("errors: " + "; ".join(str(item) for item in errors))
    if completed.stdout:
        lines.append(completed.stdout)
    if completed.stderr:
        lines.append(completed.stderr)
    return "\n".join(lines)


def test_patch_of_inactive_scene_does_not_replace_the_active_state(tmp_path):
    backend = _backend(tmp_path)
    try:
        active = backend.active_scene_id()
        other = backend.create_scene(structure=backend.get_state(active)["structure"], label="Other")["id"]
        backend.set_active_scene(active)
        active_before = backend.get_state(active)
        backend.patch_state({"display_mode": "unit_cell", "atom_scale": 1.7}, scene_id=other)
        assert backend.current_state["scene_id"] == active
        assert backend.get_state(active)["display_mode"] == active_before["display_mode"]
        assert backend.get_state(active)["atom_scale"] == active_before["atom_scale"]
        assert backend.get_state(other)["display_mode"] == "unit_cell"
        assert backend.get_state(other)["atom_scale"] == pytest.approx(1.7)
        pending = backend.pending_state
        assert pending is None or pending.get("scene_id") == active
    finally:
        backend.close()


def test_reset_keeps_the_active_projection(tmp_path):
    backend = _backend(tmp_path)
    try:
        backend.set_projection("orthographic", broadcast=False)
        canonical = backend.default_camera()
        zoomed = dict(backend.get_camera())
        zoomed["eye"] = {axis: float(zoomed["eye"][axis]) * 0.25 for axis in ("x", "y", "z")}
        backend.set_camera(zoomed, broadcast=False)
        reset = backend.camera_action("reset", broadcast=False)
        assert reset["projection"]["type"] == "orthographic"
        assert backend.get_state()["projection"] == "orthographic"
        for axis in ("x", "y", "z"):
            assert reset["eye"][axis] == pytest.approx(canonical["eye"][axis])
    finally:
        backend.close()


def child_journal_patch_race() -> None:
    """Process entry: race camera patches against journal reads, then exit."""
    root = Path(sys.argv[1])
    report_path = Path(sys.argv[2])
    try:
        _journal_patch_race(root, report_path)
    except Exception as exc:  # noqa: BLE001 - the parent report is the failure
        _write_report(
            report_path,
            {
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
                "writer_iterations": 0,
                "reader_iterations": 0,
                "elapsed_s": 0,
                "errors": [],
            },
        )
        os._exit(1)


def _journal_patch_race(root: Path, report_path: Path) -> None:
    backend = ViewerBackend(preset_path=str(root / "preset.json"), root_dir=str(root))
    state = backend.get_state()
    figure = {
        "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
        "layout": {"scene": {"camera": state.get("camera") or {}}},
    }
    backend.broadcast_figure(scene_id=state["scene_id"], figure=figure, state=state)
    backend_lock = _WaitTrackingLock(backend._lock)
    journal_lock = _WaitTrackingLock(backend._figure_broadcast_lock)
    backend._lock = backend_lock
    backend._figure_broadcast_lock = journal_lock
    progress = {"writer": 0, "reader": 0}
    errors: list[str] = []
    error_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def writer() -> None:
        try:
            barrier.wait(timeout=5)
            for index in range(_RACE_ITERATIONS):
                eye = 1.0 + (index % 5) * 0.01
                backend.patch_state(
                    {
                        "camera": {
                            "eye": {"x": eye, "y": 1.0, "z": 1.0},
                            "center": {"x": 0, "y": 0, "z": 0},
                            "up": {"x": 0, "y": 0, "z": 1},
                        }
                    },
                    broadcast=True,
                )
                progress["writer"] = index + 1
        except Exception as exc:  # noqa: BLE001 - the parent report is the failure
            with error_lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    def reader() -> None:
        try:
            barrier.wait(timeout=5)
            for index in range(_RACE_ITERATIONS):
                backend.figure_broadcasts_since(0)
                backend.latest_figure_broadcast()
                progress["reader"] = index + 1
        except Exception as exc:  # noqa: BLE001 - the parent report is the failure
            with error_lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    writer_thread = threading.Thread(target=writer, name="journal-race-writer", daemon=True)
    reader_thread = threading.Thread(target=reader, name="journal-race-reader", daemon=True)
    started = time.monotonic()
    last_heartbeat = 0.0
    writer_thread.start()
    reader_thread.start()

    def on_sample(detail: str) -> None:
        nonlocal last_heartbeat
        now = time.monotonic()
        if now - last_heartbeat < _HEARTBEAT_SECONDS:
            return
        last_heartbeat = now
        with error_lock:
            captured = list(errors)
        _write_report(
            report_path,
            {
                "status": "running",
                "detail": detail,
                "writer_iterations": progress["writer"],
                "reader_iterations": progress["reader"],
                "elapsed_s": round(now - started, 3),
                "errors": captured,
            },
        )

    status, detail = _watch_opposite_wait(
        backend_lock,
        journal_lock,
        writer_thread,
        reader_thread,
        started,
        on_sample=on_sample,
        self_timeout=None,
    )
    elapsed = round(time.monotonic() - started, 3)
    with error_lock:
        captured = list(errors)
    if status == "deadlock":
        _write_report(
            report_path,
            {
                "status": "deadlock",
                "detail": detail,
                "writer_iterations": progress["writer"],
                "reader_iterations": progress["reader"],
                "elapsed_s": elapsed,
                "errors": captured,
            },
        )
        os._exit(2)
    if captured or progress["writer"] != _RACE_ITERATIONS or progress["reader"] != _RACE_ITERATIONS:
        _write_report(
            report_path,
            {
                "status": "error",
                "detail": detail or "race ended before both sides finished",
                "writer_iterations": progress["writer"],
                "reader_iterations": progress["reader"],
                "elapsed_s": elapsed,
                "errors": captured,
            },
        )
        os._exit(1)
    backend.close()
    _write_report(
        report_path,
        {
            "status": "ok",
            "detail": "",
            "writer_iterations": progress["writer"],
            "reader_iterations": progress["reader"],
            "elapsed_s": elapsed,
            "errors": [],
        },
    )


def test_journal_read_does_not_deadlock_with_state_patch(tmp_path: Path):
    """Camera patches and journal reads must not take the locks in opposite orders.

    The child exits on a latched opposite-lock wait. A slow copy of the journal
    window keeps the iteration counters moving and is not reported as a deadlock.
    The parent kills the child at a hard cap so a stalled worker cannot outlive
    the test the way a non-daemon ``join`` timeout did.
    """
    report_path = tmp_path / "report.json"
    completed = _run_child(
        "child_journal_patch_race",
        str(tmp_path),
        str(report_path),
        timeout=_RACE_CHILD_TIMEOUT_SECONDS,
    )
    report = _load_report(report_path)
    assert completed.returncode == 0 and report.get("status") == "ok", _format_race_failure(
        completed, report
    )
    assert report["writer_iterations"] == _RACE_ITERATIONS
    assert report["reader_iterations"] == _RACE_ITERATIONS


def child_opposite_lock_order() -> None:
    """Process entry: two locks taken in opposite orders must be reported."""
    report_path = Path(sys.argv[1])
    backend_lock = _WaitTrackingLock(threading.Lock())
    journal_lock = _WaitTrackingLock(threading.Lock())
    writer_in = threading.Event()
    reader_in = threading.Event()

    def writer() -> None:
        with backend_lock:
            writer_in.set()
            if not reader_in.wait(timeout=5):
                return
            with journal_lock:
                return

    def reader() -> None:
        with journal_lock:
            reader_in.set()
            if not writer_in.wait(timeout=5):
                return
            with backend_lock:
                return

    writer_thread = threading.Thread(target=writer, name="opposite-writer", daemon=True)
    reader_thread = threading.Thread(target=reader, name="opposite-reader", daemon=True)
    started = time.monotonic()
    writer_thread.start()
    reader_thread.start()
    status, detail = _watch_opposite_wait(
        backend_lock,
        journal_lock,
        writer_thread,
        reader_thread,
        started,
        on_sample=lambda _detail: None,
        self_timeout=_DETECTOR_SELF_TIMEOUT_SECONDS,
    )
    _write_report(
        report_path,
        {
            "status": status if status != "finished" else "error",
            "detail": detail or "threads ended without an opposite-lock stall",
            "writer_iterations": 0,
            "reader_iterations": 0,
            "elapsed_s": round(time.monotonic() - started, 3),
            "errors": [],
        },
    )
    os._exit(2 if status == "deadlock" else 1)


def test_opposite_lock_order_stall_exits_without_hanging(tmp_path: Path):
    """The watchdog must name a real opposite-order stall and the child must exit."""
    report_path = tmp_path / "report.json"
    started = time.monotonic()
    completed = _run_child(
        "child_opposite_lock_order",
        str(report_path),
        timeout=_OPPOSITE_ORDER_CHILD_TIMEOUT_SECONDS,
    )
    elapsed = time.monotonic() - started
    report = _load_report(report_path)
    assert completed.returncode == 2, _format_race_failure(completed, report)
    assert report.get("status") == "deadlock"
    assert report.get("detail") == (
        "writer waited on the journal lock while reader waited on the backend lock"
    )
    assert elapsed < _OPPOSITE_ORDER_CHILD_TIMEOUT_SECONDS


def test_selection_commit_enqueues_render_without_the_backend_lock(tmp_path):
    """A click commit must not hold the backend lock while enqueueing a frame.

    The render worker publishes by taking its own lock and then reading
    backend state. Holding both in the opposite order deadlocks shutdown.
    """

    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        revision = backend.get_state(scene_id).get("target_revision", 0)
        worker = backend._render_worker
        entered = threading.Event()
        finished_read = threading.Event()
        errors: list[object] = []
        arm = {"on": False}

        def request(state, force=False):
            if not arm["on"]:
                return True
            depth = int(getattr(backend._backend_lock_tls, "depth", 0) or 0)
            if depth != 0:
                errors.append(f"backend lock depth {depth}")
            entered.set()
            if not finished_read.wait(timeout=3):
                errors.append("state read did not finish")
            return True

        worker.request_figure_build = request
        assert backend._commit_selection_event(
            {
                "targets": [{"kind": "atom", "id": "atom:a"}],
                "mode": "replace",
                "target_revision": revision,
                "ts": 10,
            },
            scene_id,
        )

        def reader():
            try:
                if not entered.wait(timeout=3):
                    errors.append("render enqueue did not start")
                    return
                if not worker._lock.acquire(timeout=2):
                    errors.append("worker lock stayed busy")
                    return
                try:
                    done = threading.Event()

                    def read():
                        try:
                            backend.get_state(scene_id)
                        except Exception as exc:  # noqa: BLE001
                            errors.append(exc)
                        finally:
                            done.set()

                    threading.Thread(target=read).start()
                    if not done.wait(timeout=2):
                        errors.append("get_state blocked while the worker lock was held")
                finally:
                    worker._lock.release()
            finally:
                finished_read.set()

        arm["on"] = True
        thread = threading.Thread(target=reader)
        thread.start()
        try:
            assert backend._commit_selection_event(
                {"mode": "clear", "targets": [], "target_revision": revision, "ts": 20},
                scene_id,
            )
        finally:
            finished_read.set()
            thread.join(timeout=3)
        assert not thread.is_alive()
        assert errors == []
        assert backend.get_selection(scene_id)["targets"] == []
    finally:
        backend.close()


def test_render_publish_does_not_hold_the_worker_lock(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    try:
        state = backend.get_state()
        worker = backend._render_worker
        observed: dict[str, bool] = {}

        class _Figure:
            def to_plotly_json(self):
                return {
                    "data": [{"type": "scatter3d", "x": [0], "y": [0], "z": [0]}],
                    "layout": {"scene": {}},
                }

        def figure_for_state(*_args, **_kwargs):
            return _Figure(), None

        def broadcast_figure(**_kwargs):
            def other():
                got = worker._lock.acquire(blocking=False)
                if got:
                    worker._lock.release()
                observed["free"] = got

            probe = threading.Thread(target=other)
            probe.start()
            probe.join(timeout=2)
            return {"type": "figure"}

        monkeypatch.setattr(backend, "figure_for_state", figure_for_state)
        monkeypatch.setattr(backend, "broadcast_figure", broadcast_figure)
        assert worker.request_figure_build(state)
        deadline = threading.Event()
        for _ in range(100):
            if "free" in observed:
                break
            deadline.wait(0.02)
        assert observed.get("free") is True
    finally:
        backend.close()
