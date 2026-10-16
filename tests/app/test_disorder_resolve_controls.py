from __future__ import annotations

from mat_viewer.app.callbacks_disorder import _coerce_count, _replica_rows


def test_disorder_count_controls_keep_safe_limits():
    assert _coerce_count(0) == 1
    assert _coerce_count(-3) == 1
    assert _coerce_count(5) == 5
    assert _coerce_count(1000) == 128


def test_no_disorder_result_has_explicit_ui_message():
    rows = _replica_rows([], status="no_disorder")

    assert len(rows) == 1
    assert "No SHELX-style disorder replicas found" in str(rows[0].children)
