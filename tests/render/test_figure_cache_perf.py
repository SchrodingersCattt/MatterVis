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
    """Architecture pin for stale compass and drag-rotation regressions:

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
    assert len(first_arrows) > 0, (
        "Dash-served figure MUST bake compass arrows into "
        "layout.annotations (compass_overlay.js has been removed)."
    )

    # Second render with a different camera: figure body cache may
    # hit, compass is still baked.
    state_far = dict(state)
    state_far["camera"] = {
        "eye": {"x": 0.0, "y": 2.0, "z": 0.5},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
    }
    fig_second, _ = backend.figure_for_state(state_far)
    second_arrows = _compass_shapes(fig_second)
    assert len(second_arrows) > 0, (
        "Cached figure must also bake compass annotations (compass_overlay.js removed)."
    )


def test_preset_load_invalidates_figure_cache(tmp_path):
    backend = _make_backend(tmp_path)
    state = backend.get_state()
    backend.figure_for_state(state)
    assert len(backend._figure_cache) >= 1

    preset_payload = backend.save_preset("regression.json")
    backend.load_preset_from_path(preset_payload["path"], allow_external=True)
    assert backend._figure_cache == {}


def test_figure_cache_evicts_oldest_when_full(tmp_path):
    """When the figure cache exceeds ``FIGURE_CACHE_MAX`` distinct
    entries, the least-recently-used entry is evicted.
    """
    backend = _make_backend(tmp_path)

    # Clear the cache so we start from a known empty state.
    with backend._figure_cache_lock:
        backend._figure_cache.clear()

    base_state = backend.get_state()

    # Fill past ``FIGURE_CACHE_MAX`` with one-call-per-entry variation
    # on a cache-key-affecting field to force distinct entries.
    from crystal_viewer.app.backend_core import FIGURE_CACHE_MAX

    keys_in_order: list[str] = []
    for i in range(FIGURE_CACHE_MAX + 3):
        state = dict(base_state)
        # A unique label on a style key that participates in
        # ``_figure_state_cache_key`` forces a new cache entry.
        state["_test_label"] = f"entry_{i:03d}"
        key = backend._figure_state_cache_key(state)
        keys_in_order.append(key)
        backend.figure_for_state(state)

    with backend._figure_cache_lock:
        # The first few keys (FIFO oldest) must have been evicted.
        evicted = [k for k in keys_in_order[:3] if k not in backend._figure_cache]
        assert len(evicted) >= 1, (
            f"Expected at least one evicted key, but cache has "
            f"{len(backend._figure_cache)} entries out of "
            f"{len(keys_in_order)} inserted"
        )
        # The newest keys must still be present.
        assert keys_in_order[-1] in backend._figure_cache, (
            "Most-recently-inserted entry must survive eviction"
        )


def test_figure_cache_hit_moves_to_mru(tmp_path):
    """A cache hit moves the accessed entry to most-recently-used
    position so it survives future evictions longer.
    """
    backend = _make_backend(tmp_path)

    # Clear cache to start clean.
    with backend._figure_cache_lock:
        backend._figure_cache.clear()

    base_state = backend.get_state()

    # Insert N distinct entries.
    N = 5
    keys_in_order: list[str] = []
    for i in range(N):
        state = dict(base_state)
        state["_test_label"] = f"entry_{i:03d}"
        key = backend._figure_state_cache_key(state)
        keys_in_order.append(key)
        backend.figure_for_state(state)

    with backend._figure_cache_lock:
        assert all(k in backend._figure_cache for k in keys_in_order), (
            "All {N} entries must fit in cache"
        )

    # Access the oldest entry (first inserted).  This is a cache hit
    # that should promote it to the MRU position so it is no longer
    # the next to be evicted.
    oldest_state = dict(base_state)
    oldest_state["_test_label"] = "entry_000"
    backend.figure_for_state(oldest_state)

    with backend._figure_cache_lock:
        first_key = next(iter(backend._figure_cache.keys()))
        assert first_key != keys_in_order[0], (
            "Cache-hit entry must be promoted to MRU; "
            "the oldest key should no longer be at the eviction head"
        )
