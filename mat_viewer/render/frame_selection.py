"""Select trajectory frames using the public CLI slice contract."""

from __future__ import annotations


def parse_frame_indices(
    frame_count: int,
    frame_range: str | None,
    stride: int,
) -> list[int]:
    if stride <= 0:
        raise ValueError("--stride must be greater than zero")
    if frame_range is None:
        indices = list(range(frame_count))
    else:
        parts = frame_range.split(":")
        if len(parts) not in (2, 3):
            raise ValueError("--frame-range must use START:STOP[:STEP]")
        try:
            values = [int(value) if value else None for value in parts]
        except ValueError as exc:
            raise ValueError("--frame-range values must be integers") from exc
        if len(values) == 2:
            values.append(None)
        selection = slice(*values)
        start, stop, step = selection.indices(frame_count)
        indices = list(range(start, stop, step))
    return indices[::stride]
