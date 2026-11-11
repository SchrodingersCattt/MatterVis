"""Harness rules that keep real-app browser tests deterministic.

These checks are source contracts. They do not launch Chrome. A regression
that puts a timeout object in ``waitForFunction``'s argument slot, or that
passes an async predicate to it, fails here instead of timing out in CI.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from tests.app.real_app_server import _port_still_bound, start_mattervis

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "tests" / "app"
REAL_APP_TEST = APP_JS / "test_real_app_browser_regression.py"


def _calls(source: str) -> list[str]:
    """Return the argument text of each ``waitForFunction`` call."""
    calls: list[str] = []
    needle = "waitForFunction("
    start = 0
    while True:
        index = source.find(needle, start)
        if index < 0:
            break
        if index > 0 and (source[index - 1].isalnum() or source[index - 1] == "_"):
            start = index + len(needle)
            continue
        depth = 1
        quote = ""
        escaped = False
        cursor = index + len(needle)
        body_start = cursor
        while cursor < len(source) and depth:
            char = source[cursor]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
            elif char in ("'", '"', "`"):
                quote = char
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            cursor += 1
        calls.append(source[body_start:cursor - 1])
        start = cursor
    return calls


def _split_args(body: str) -> list[str]:
    args: list[str] = []
    depth = 0
    quote = ""
    escaped = False
    start = 0
    for index, char in enumerate(body):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(body[start:index].strip())
            start = index + 1
    tail = body[start:].strip()
    if tail:
        args.append(tail)
    return args


def wait_for_function_violations(source: str) -> list[str]:
    """Return human-readable contract breaks in ``source``."""
    violations: list[str] = []
    for body in _calls(source):
        args = _split_args(body)
        if not args:
            continue
        if args[0].lstrip().startswith("async"):
            violations.append("async waitForFunction predicate: " + args[0][:80])
        if len(args) == 2 and args[1].lstrip().startswith("{") and "timeout" in args[1]:
            violations.append("timeout object passed as the argument: " + args[1][:80])
    return violations


def test_timeout_object_is_not_a_wait_for_function_argument():
    bad = "page.waitForFunction(() => ready, { timeout: 90000 });"
    good = "page.waitForFunction(() => ready, null, { timeout: 90000 });"
    assert wait_for_function_violations(bad)
    assert wait_for_function_violations(good) == []
    assert wait_for_function_violations(
        "page.waitForFunction(async () => fetch('/api/v2/state'), null);"
    )


def test_browser_runners_await_server_state_outside_wait_for_function():
    violations: list[str] = []
    for path in sorted(APP_JS.glob("*.js")):
        found = wait_for_function_violations(path.read_text(encoding="utf-8"))
        violations.extend(f"{path.name}: {item}" for item in found)
    assert violations == []


def test_two_client_sessions_use_independent_browser_contexts():
    source = (APP_JS / "real_app_browser.js").read_text(encoding="utf-8")
    assert "page.context().newPage()" not in source
    start = source.index("function isUploadPost")
    end = source.index("async function runLeak")
    body = source[start:end]
    assert "browser.newContext(" in body
    assert "context.newPage(" in body
    assert "finally" in body
    assert 'pathname === "/api/v2/upload"' in body
    assert "porous_framework" not in body
    assert "waitForThisUpload(" in body


def test_real_app_browser_tests_do_not_share_a_server():
    source = REAL_APP_TEST.read_text(encoding="utf-8")
    assert 'scope="module"' not in source
    assert "def _port(" not in source
    assert "start_mattervis" in source


def test_isolated_server_publishes_status_and_releases_its_port(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    running = start_mattervis(str(tmp_path), [])
    port = running.port
    try:
        assert _port_still_bound(port)
        with urllib.request.urlopen(running.url + "/_test/render-status", timeout=10) as response:
            payload = json.loads(response.read().decode())
        assert "pending" in payload
        assert "cursor" in payload
        assert isinstance(payload["threads"], list)
    finally:
        running.stop()
    assert not _port_still_bound(port)


def test_scene_switch_census_trace_does_not_capture_screenshots():
    """Screenshot traces stall WebGL scene swaps across the 100-switch census."""
    source = (APP_JS / "real_app_browser.js").read_text(encoding="utf-8")
    marker = 'const screenshots = mode !== "leak";'
    assert marker in source
    start = source.index("context.tracing.start")
    call = source[start:source.index(");", start)]
    assert "screenshots" in call and "snapshots: true" in call
    assert "screenshots: true, snapshots: true" not in source


def test_failed_bind_closes_the_app_before_the_port_is_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    running = start_mattervis(str(tmp_path), [])
    before = {thread.name for thread in threading.enumerate()}
    second = tmp_path / "second"
    second.mkdir()
    try:
        with pytest.raises(SystemExit):
            start_mattervis(str(second), [], port=running.port)
        leaked = {
            thread.name
            for thread in threading.enumerate()
            if thread.name.startswith("mattervis") and thread.name not in before
        }
        assert leaked == set()
    finally:
        running.stop()
