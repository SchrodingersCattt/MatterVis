from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mat_viewer.render.animation_time import (
    AnimationTimeSpec,
    draw_time_label,
    resolve_animation_times,
)


def _frame(index: int, **info):
    return SimpleNamespace(index=index, info=info)


def test_physical_time_metadata_is_converted_without_md_timestep() -> None:
    series = resolve_animation_times(
        [_frame(0, time_ps=0.25), _frame(5, time_ps=0.75)],
        AnimationTimeSpec(display_unit="fs"),
    )

    assert series.values == pytest.approx((250.0, 750.0))
    assert series.labels == ("t = 250 fs", "t = 750 fs")
    assert series.source == "frame_info.time_ps"
    assert series.simulation_steps is None


def test_lammps_timestep_metadata_wins_over_selection_stride() -> None:
    series = resolve_animation_times(
        [_frame(3, timestep=500), _frame(9, timestep=800)],
        AnimationTimeSpec(
            display_unit="ps",
            time_step=0.5,
            time_step_unit="fs",
            dump_frequency=100,
        ),
    )

    assert series.values == pytest.approx((0.25, 0.4))
    assert series.simulation_steps == (500.0, 800.0)
    assert series.source == "frame_info.timestep"


def test_frame_index_fallback_uses_dump_frequency_once() -> None:
    series = resolve_animation_times(
        [_frame(2), _frame(6)],
        AnimationTimeSpec(
            display_unit="ps",
            time_step=2.0,
            time_step_unit="fs",
            dump_frequency=50,
            first_frame_step=1000,
        ),
    )

    assert series.simulation_steps == (1100.0, 1300.0)
    assert series.values == pytest.approx((2.2, 2.6))
    assert series.source == "frame_index*dump_frequency"


def test_missing_step_mapping_fails_instead_of_using_video_fps() -> None:
    with pytest.raises(ValueError, match="dump-frequency"):
        resolve_animation_times(
            [_frame(0), _frame(1)],
            AnimationTimeSpec(
                display_unit="ps",
                time_step=1.0,
                time_step_unit="fs",
            ),
        )


def test_time_label_changes_only_a_corner_region() -> None:
    pillow = pytest.importorskip("PIL.Image")
    source = pillow.fromarray(np.full((160, 240, 4), 255, dtype=np.uint8), mode="RGBA")

    labelled = np.asarray(draw_time_label(source, "t = 2.5 ps", "top-left"))
    changed = np.any(labelled != np.asarray(source), axis=2)

    assert changed.any()
    ys, xs = np.where(changed)
    assert xs.max() < 240 // 2
    assert ys.max() < 160 // 2
