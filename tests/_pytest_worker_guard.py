"""Stop an xdist worker crash from stalling the suite.

pytest-timeout's thread method ends a wedged test with ``os._exit``.
pytest-xdist reports that as ``node down: Not properly terminated`` and,
under ``--dist loadfile``, runs the whole file again on a new worker.
On the Python 3.12 full-test job for commit ``eebf809`` that replacement
left the controller and the surviving workers alive with no further
reports for the rest of the six-hour job limit.

Two bounds close that hole:

* A crashed worker is not replaced. The session records the node id and
  shuts down, so the failure names the test instead of scheduling it again.
* The controller keeps its own clock. The worker timer dumps stacks with
  ``faulthandler`` and exits without taking pytest's terminal lock, which
  the stock timer can block on. If reports still stop after that test's
  own timeout, the controller exits as well. A hang outside a test item,
  such as interpreter shutdown joining a non-daemon thread, uses the
  silence bound because no per-test timer is armed then.
"""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

_STATE_ENV = "MATTERVIS_PYTEST_STALL_DIR"
_SILENCE_ENV = "MATTERVIS_PYTEST_STALL_SILENCE"
_GRACE_ENV = "MATTERVIS_PYTEST_STALL_GRACE"
_COLLECTION_ENV = "MATTERVIS_PYTEST_STALL_COLLECTION"
_DEFAULT_SILENCE = 90.0
_DEFAULT_GRACE = 30.0
_DEFAULT_COLLECTION = 180.0

_stop = threading.Event()
_lock = threading.Lock()
_last_progress = 0.0
_saw_test = False
_recent: list[str] = []


def _seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _state_dir() -> Path:
    override = os.environ.get(_STATE_ENV)
    path = Path(override) if override else Path(tempfile.mkdtemp(prefix="mv-pytest-stall-"))
    if not override:
        os.environ[_STATE_ENV] = str(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _controller_state_dir() -> Path:
    """A nested pytest must not watch the parent run's worker files."""
    path = Path(tempfile.mkdtemp(prefix="mv-pytest-stall-"))
    os.environ[_STATE_ENV] = str(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path() -> Path:
    return _state_dir() / f"worker-{os.getpid()}.json"


def _item_timeout(item: pytest.Item) -> float:
    marker = item.get_closest_marker("timeout")
    if marker is not None and marker.args:
        try:
            value = float(marker.args[0])
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    raw = item.config.getini("timeout")
    try:
        value = float(raw) if raw else 0.0
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else _seconds(_SILENCE_ENV, _DEFAULT_SILENCE)


def _controller(config: pytest.Config) -> bool:
    return not hasattr(config, "workerinput")


def _note(nodeid: str) -> None:
    global _last_progress, _saw_test
    with _lock:
        _last_progress = time.monotonic()
        _saw_test = True
        _recent.append(nodeid)
        del _recent[:-8]


def _deadline_from_disk(now: float) -> float | None:
    directory = os.environ.get(_STATE_ENV)
    if not directory:
        return None
    grace = _seconds(_GRACE_ENV, _DEFAULT_GRACE)
    latest: float | None = None
    for path in Path(directory).glob("worker-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            started = float(payload["started"])
            timeout = float(payload["timeout"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        # ``started`` is wall time. Compare against wall time, not the
        # monotonic clock used for controller silence.
        end = started + timeout + grace
        if latest is None or end > latest:
            latest = end
    if latest is None:
        return None
    return latest - now


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    option = getattr(config, "option", None)
    if option is not None and getattr(option, "maxworkerrestart", None) is None:
        option.maxworkerrestart = "0"
    if not _controller(config):
        return
    _controller_state_dir()
    global _last_progress
    with _lock:
        _last_progress = time.monotonic()
    thread = threading.Thread(target=_watch, name="mv-pytest-stall", daemon=True)
    thread.start()


def _watch() -> None:
    global _last_progress
    while not _stop.wait(0.5):
        now_mono = time.monotonic()
        now_wall = time.time()
        with _lock:
            last = _last_progress
            saw_test = _saw_test
            recent = list(_recent)
        disk_remaining = _deadline_from_disk(now_wall)
        if disk_remaining is not None and disk_remaining > 0:
            # The in-flight test is still inside its own timeout. That is
            # progress even when no newer test has started, so a slow test
            # does not look idle the moment its state file is removed.
            with _lock:
                _last_progress = now_mono
            continue
        if disk_remaining is None:
            silence = (
                _seconds(_COLLECTION_ENV, _DEFAULT_COLLECTION)
                if not saw_test
                else _seconds(_SILENCE_ENV, _DEFAULT_SILENCE)
            )
            if now_mono - last < silence:
                continue
        lines = [
            "pytest stalled: the controller received no further test reports",
        ]
        if recent:
            lines.append("last tests:")
            lines.extend(f"  {nodeid}" for nodeid in recent)
        directory = os.environ.get(_STATE_ENV)
        if directory:
            for path in sorted(Path(directory).glob("worker-*.json")):
                try:
                    lines.append(path.read_text(encoding="utf-8").strip())
                except OSError:
                    continue
        sys.stderr.write("\n".join(lines) + "\n")
        sys.stderr.flush()
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        os._exit(3)


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_set_timer(item: pytest.Item, settings: object) -> bool | None:
    method = getattr(settings, "method", None)
    timeout = getattr(settings, "timeout", None)
    if method != "thread" or not timeout or float(timeout) <= 0:
        return None
    done = threading.Event()

    def fire() -> None:
        if done.is_set():
            return
        sys.stderr.write(
            f"\nTimeout (>{float(timeout):g}s) from worker guard: {item.nodeid}\n"
        )
        sys.stderr.flush()
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:
            pass
        os._exit(1)

    timer = threading.Timer(float(timeout), fire)
    timer.daemon = True
    timer.name = f"mv-pytest-timeout {item.nodeid}"

    def cancel() -> None:
        done.set()
        timer.cancel()

    item.cancel_timeout = cancel  # type: ignore[attr-defined]
    timer.start()
    return True


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item):
    payload = {
        "nodeid": item.nodeid,
        "timeout": _item_timeout(item),
        "started": time.time(),
    }
    path = _state_path()
    path.write_text(json.dumps(payload), encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@pytest.hookimpl
def pytest_runtest_logstart(nodeid: str, location: object) -> None:
    _note(nodeid)


@pytest.hookimpl
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _stop.set()


def pytest_unconfigure(config: pytest.Config) -> None:
    _stop.set()
