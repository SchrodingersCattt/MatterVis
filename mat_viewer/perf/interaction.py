"""Small, dependency-free probes for pointer/click paint latency.

The browser viewer has two clocks for an interaction: the input event and the
first paint that reflects it.  Timing only the callback (or only a backend
figure build) misses the useful number.  This module keeps the measurement
boundary explicit without pulling Playwright or a browser into the library:
callers provide an input action and a paint waiter, and optionally a topology
counter.  The same probe can therefore be used from a browser test and from a
backend regression test.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from time import perf_counter
from typing import Any, Callable


Clock = Callable[[], float]
Action = Callable[[], Any]
PaintWaiter = Callable[[Any], Any]
Counter = Callable[[], int]


@dataclass(frozen=True)
class InteractionLatency:
    """Summary of one interaction probe.

    Values are milliseconds from ``action`` entry through the return of the
    supplied ``paint`` waiter.  ``topology_recomputes`` is optional and counts
    calls observed around the whole probe, not just the paint callback.
    """

    kind: str
    samples_ms: tuple[float, ...]
    topology_recomputes: int | None = None

    @property
    def count(self) -> int:
        return len(self.samples_ms)

    @property
    def mean_ms(self) -> float:
        return float(mean(self.samples_ms)) if self.samples_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.samples_ms:
            return 0.0
        values = sorted(self.samples_ms)
        # Nearest-rank p95 keeps the result deterministic for small browser
        # samples and avoids interpolation between event-loop observations.
        index = max(0, min(len(values) - 1, int((len(values) * 95 + 99) // 100) - 1))
        return float(values[index])

    @property
    def max_ms(self) -> float:
        return float(max(self.samples_ms, default=0.0))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "count": self.count,
            "samples_ms": list(self.samples_ms),
            "mean_ms": self.mean_ms,
            "p95_ms": self.p95_ms,
            "max_ms": self.max_ms,
            "topology_recomputes": self.topology_recomputes,
        }


def measure_interaction(
    action: Action,
    paint: PaintWaiter | None = None,
    *,
    kind: str = "interaction",
    repeat: int = 1,
    clock: Clock = perf_counter,
    topology_counter: Counter | None = None,
) -> InteractionLatency:
    """Measure input-to-paint latency for a repeatable action.

    ``paint`` should return only after the browser or renderer has observed the
    changed visual state.  If it is omitted, the action's return marks the end
    of the sample, which is useful for synchronous backend smoke tests.  A
    non-positive ``repeat`` is rejected so an accidentally empty benchmark
    cannot look like a zero-latency success.
    """

    try:
        count = int(repeat)
    except (TypeError, ValueError) as exc:
        raise ValueError("repeat must be a positive integer") from exc
    if count < 1:
        raise ValueError("repeat must be a positive integer")
    before = topology_counter() if topology_counter is not None else None
    samples: list[float] = []
    for _ in range(count):
        started = clock()
        result = action()
        if paint is not None:
            paint(result)
        samples.append(max(0.0, (clock() - started) * 1000.0))
    after = topology_counter() if topology_counter is not None else None
    recomputes = (int(after) - int(before)) if before is not None and after is not None else None
    return InteractionLatency(str(kind), tuple(samples), recomputes)


def measure_pointer_latency(
    action: Action,
    paint: PaintWaiter | None = None,
    **kwargs: Any,
) -> InteractionLatency:
    """Measure a pointer hover/press path with an explicit ``kind`` label."""

    kwargs.setdefault("kind", "pointer")
    return measure_interaction(action, paint, **kwargs)


def measure_click_latency(
    action: Action,
    paint: PaintWaiter | None = None,
    **kwargs: Any,
) -> InteractionLatency:
    """Measure a click-to-paint path with an explicit ``kind`` label."""

    kwargs.setdefault("kind", "click")
    return measure_interaction(action, paint, **kwargs)


def require_paint_only(report: InteractionLatency) -> InteractionLatency:
    """Assert that a measured interaction did not recompute topology.

    A missing counter is allowed because browser-only probes cannot observe
    the backend worker.  When a counter was supplied, any increment indicates
    that a selection/hover action escaped the paint-only path.
    """

    if report.topology_recomputes not in (None, 0):
        raise AssertionError(
            f"{report.kind} recomputed topology {report.topology_recomputes} time(s)"
        )
    return report


__all__ = [
    "InteractionLatency",
    "measure_click_latency",
    "measure_interaction",
    "measure_pointer_latency",
    "require_paint_only",
]
