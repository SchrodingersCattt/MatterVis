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


def test_cached_figure_skips_baked_compass_in_dash_path(tmp_path):
    """Architecture pin (was "axis错了" / "compass 不动" / "拖拽分子不转"):

    The Dash-served figure must NOT carry compass arrows in
    ``layout.annotations`` because ``compass_overlay.js`` now paints
    them live into a sibling SVG layer. Baking them into Plotly
    annotations forces a ``Plotly.relayout`` per drag frame, which
    interrupts gl3d's render and freezes the molecule rotation
    (verified via Playwright: 6 mid-drag screenshots all
    byte-identical when the compass was Plotly-baked).

    Instead the compass *meta* (lattice matrix + sizing) lives on
    ``layout.meta.compass`` so the JS can reproject without a
    server round-trip.
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
    assert first_arrows == [], (
        "Dash-served figure must NOT bake compass arrows into "
        "layout.annotations; the SVG overlay handles them live."
    )
    # The JS-consumed payload must still be present.
    meta = getattr(fig_first.layout, "meta", None)
    if hasattr(meta, "to_plotly_json"):
        meta = meta.to_plotly_json()
    assert isinstance(meta, dict) and meta.get("compass"), (
        "layout.meta.compass must be populated so compass_overlay.js "
        "can reproject the triad client-side."
    )

    # Second render with a different camera: figure body cache may
    # hit, but neither figure has compass annotations and meta still
    # exists.
    state_far = dict(state)
    state_far["camera"] = {
        "eye": {"x": 0.0, "y": 2.0, "z": 0.5},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
    }
    fig_second, _ = backend.figure_for_state(state_far)
    second_arrows = _compass_shapes(fig_second)
    assert second_arrows == [], (
        "Cached figure must also stay free of baked compass annotations."
    )


def test_preset_load_invalidates_figure_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state = backend.get_state()
    backend.figure_for_state(state)
    assert len(backend._figure_cache) >= 1

    preset_payload = backend.save_preset("regression.json")
    backend.load_preset_from_path(preset_payload["path"], allow_external=True)
    assert backend._figure_cache == {}
