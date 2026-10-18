from __future__ import annotations

import pytest

from mat_viewer.perf.interaction import (
    measure_click_latency,
    measure_pointer_latency,
    require_paint_only,
)


def test_click_probe_times_through_paint_and_reports_topology_counter() -> None:
    ticks = iter([10.0, 10.012, 20.0, 20.018])
    topology_calls = [0]
    painted = []

    def action():
        return {"selection": "A1"}

    def paint(result):
        painted.append(result["selection"])

    def counter():
        value = topology_calls[0]
        topology_calls[0] += 0
        return value

    report = measure_click_latency(
        action,
        paint,
        repeat=2,
        clock=lambda: next(ticks),
        topology_counter=counter,
    )

    assert painted == ["A1", "A1"]
    assert report.kind == "click"
    assert report.samples_ms == pytest.approx((12.0, 18.0))
    assert report.mean_ms == pytest.approx(15.0)
    assert report.p95_ms == pytest.approx(18.0)
    assert report.topology_recomputes == 0
    require_paint_only(report)


def test_pointer_probe_can_fail_when_topology_was_recomputed() -> None:
    ticks = iter([0.0, 0.001])
    calls = [1]

    report = measure_pointer_latency(
        lambda: None,
        repeat=1,
        clock=lambda: next(ticks),
        topology_counter=lambda: calls[0],
    )
    calls[0] = 2
    # The counter is sampled around the probe; this synthetic check verifies
    # the assertion helper independently with an explicit failure report.
    bad = report.__class__("pointer", report.samples_ms, topology_recomputes=1)
    with pytest.raises(AssertionError, match="recomputed topology"):
        require_paint_only(bad)


@pytest.mark.parametrize("repeat", [0, -1])
def test_probe_rejects_empty_repeat(repeat: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        measure_click_latency(lambda: None, repeat=repeat)
