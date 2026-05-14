"""Regression tests for the ``_figure_cache`` reuse behaviour.

Historically every state mutation called ``_bump_version()`` which
*also* wiped the entire figure cache. That made every tab switch and
every cancelled slider tweak cost a full ~400 ms ``build_figure``
rebuild. The cache key already uniquely identifies the figure body
content, so the broad invalidation is no longer needed.
"""

from __future__ import annotations

from crystal_viewer.app import ViewerBackend


def _make_backend(tmp_path):
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def test_bump_version_does_not_clear_figure_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state = backend.get_state()
    backend.figure_for_state(state)
    cache_size_before = len(backend._figure_cache)
    assert cache_size_before >= 1

    backend._bump_version()
    assert len(backend._figure_cache) == cache_size_before


def test_figure_cache_hits_across_version_bumps(tmp_path):
    backend = _make_backend(tmp_path)
    state_a = backend.get_state()
    fig_first, _ = backend.figure_for_state(state_a)
    snapshot = len(backend._figure_cache)
    assert snapshot >= 1

    backend._bump_version()
    backend._bump_version()
    state_b = backend.get_state()
    fig_second, _ = backend.figure_for_state(state_b)
    assert len(backend._figure_cache) == snapshot
    assert fig_first.to_dict()["data"] == fig_second.to_dict()["data"]


def test_camera_change_does_not_invalidate_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state_a = backend.get_state()
    backend.figure_for_state(state_a)
    snapshot = len(backend._figure_cache)

    state_b = dict(state_a)
    state_b["camera"] = {"eye": {"x": 2.0, "y": 1.0, "z": 0.5}}
    backend.figure_for_state(state_b)
    assert len(backend._figure_cache) == snapshot


_COMPASS_ITEM_NAME = "mv_compass"


def _compass_shapes(fig) -> list[dict]:
    """Pull compass arrow annotations from ``fig.layout``.

    The new single-anchor compass renders each axis as a Plotly
    annotation arrow (``showarrow=True``) tagged with
    ``name="mv_compass"`` so the clientside reprojection handler can
    find them. The previous row-stacked layout used separate
    ``shape.type="line"`` shafts; that path is gone.
    """
    out: list[dict] = []
    for ann in fig.layout.annotations or []:
        if getattr(ann, "name", None) != _COMPASS_ITEM_NAME:
            continue
        if not getattr(ann, "showarrow", False):
            continue
        out.append(
            {
                "x": float(ann.x),
                "y": float(ann.y),
                "ax": float(ann.ax),
                "ay": float(ann.ay),
            }
        )
    return out


def test_cached_figure_refreshes_compass_under_live_camera(tmp_path):
    """Regression for the "axis错了" report: a cached figure was
    serving stale paper-coord compass arrows because the in-figure
    overlay is camera-dependent. ``figure_for_state`` now re-projects
    the compass after the cache hit so the arrows match the requested
    camera even when the heavy mesh body is reused verbatim.
    """
    backend = _make_backend(tmp_path)
    state = backend.get_state()
    state["display_options"] = list(set((state.get("display_options") or []) + ["axes"]))
    state["camera"] = {
        "eye": {"x": 2.0, "y": 0.0, "z": 0.5},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
    }
    fig_first, _ = backend.figure_for_state(state)
    first_arrows = _compass_shapes(fig_first)
    assert first_arrows, "expected compass arrows on initial render"

    state_far = dict(state)
    state_far["camera"] = {
        "eye": {"x": 0.0, "y": 2.0, "z": 0.5},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
    }
    fig_second, _ = backend.figure_for_state(state_far)
    second_arrows = _compass_shapes(fig_second)
    assert second_arrows, "expected compass arrows on second render"
    assert first_arrows != second_arrows, (
        "compass arrows should reproject when the live camera changes "
        "even when the figure-body cache hits"
    )


def test_preset_load_invalidates_figure_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state = backend.get_state()
    backend.figure_for_state(state)
    assert len(backend._figure_cache) >= 1

    preset_payload = backend.save_preset("regression.json")
    backend.load_preset_from_path(preset_payload["path"], allow_external=True)
    assert backend._figure_cache == {}
