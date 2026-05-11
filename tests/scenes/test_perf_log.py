"""Tests for the in-process perf-log + the /api/v1/perf endpoint.

Background
----------
On 2026-05-10 the user complained that uploads were slow and asked
"do you even have logs?". We now record a timestamped event for each
callback / upload step in :mod:`crystal_viewer.perf_log`. These tests
lock in the contract:

* :func:`record` is FIFO with monotonic ``seq`` numbers.
* :func:`time_block` records the elapsed time in milliseconds.
* ``GET /api/v1/perf`` returns events JSON-encoded, supports
  ``?since=`` for incremental polls, and ``POST /api/v1/perf/clear``
  empties the buffer.
"""
from __future__ import annotations

import os
import tempfile
import time

import pytest

from crystal_viewer import perf_log


@pytest.fixture(autouse=True)
def _isolated_log_file(monkeypatch, tmp_path):
    """Point the on-disk log at a tmp file so tests don't collide on
    ``/tmp/cv-perf.log`` and don't leak across runs."""
    log_path = tmp_path / "cv-perf-test.log"
    monkeypatch.setattr(perf_log, "_LOG_PATH", str(log_path))
    perf_log.clear()
    yield
    perf_log.clear()


def test_record_returns_strictly_monotonic_seq():
    a = perf_log.record("a")
    b = perf_log.record("b")
    c = perf_log.record("c")
    assert b["seq"] == a["seq"] + 1
    assert c["seq"] == b["seq"] + 1


def test_recent_returns_chronological_order_and_filters_by_since():
    perf_log.record("first")
    middle = perf_log.record("middle")
    perf_log.record("last")

    all_events = perf_log.recent()
    assert [e["label"] for e in all_events[-3:]] == ["first", "middle", "last"]

    tail = perf_log.recent(since_seq=middle["seq"])
    assert [e["label"] for e in tail] == ["last"]


def test_time_block_records_positive_elapsed_ms():
    with perf_log.time_block("sleep_block"):
        time.sleep(0.02)
    last = perf_log.recent(limit=1)[-1]
    assert last["label"] == "sleep_block"
    # Account for clock granularity but require >=15 ms (we slept 20).
    assert last["ms"] >= 15.0
    assert last["ms"] < 1000.0


def test_record_appends_to_disk_log(tmp_path):
    perf_log.record("disk_event", info={"k": "v"})
    contents = open(perf_log.log_path()).read().strip().splitlines()
    assert any("disk_event" in line for line in contents)


def test_perf_endpoint_returns_events_and_supports_since(monkeypatch, tmp_path):
    from crystal_viewer.app import create_app

    monkeypatch.setattr(perf_log, "_LOG_PATH", str(tmp_path / "cv-perf-app.log"))
    perf_log.clear()
    app = create_app()
    server = app.server
    server.config["TESTING"] = True
    client = server.test_client()

    perf_log.record("event_one")
    seq = perf_log.latest_seq()
    perf_log.record("event_two")

    response = client.get(f"/api/v1/perf?since={seq}")
    assert response.status_code == 200
    body = response.get_json()
    labels = [e["label"] for e in body["events"]]
    assert labels == ["event_two"]
    assert body["latest_seq"] == perf_log.latest_seq()

    clear_response = client.post("/api/v1/perf/clear")
    assert clear_response.status_code == 200
    assert clear_response.get_json() == {"cleared": True}
    assert perf_log.recent() == []
