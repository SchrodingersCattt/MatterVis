"""Lock-in tests for the Phase-2 performance caches.

These caches are content-hashed and module-scoped, so they sit at the
"cheap to break, cheap to test" end of the spectrum. The contracts we
need to defend are:

1. ``auto_view_dir`` returns a usable copy on cache hit (callers are
   allowed to mutate the array).
2. The cache key is content-addressed: equal-by-content atoms hit the
   same entry (so two different ``LoadedCrystal`` instances created
   from the same CIF share the cached view direction).
3. The cache key is *correctly* discriminated: structurally distinct
   atoms must NOT collide. Otherwise we'd silently pick the wrong
   view direction for one structure when two are loaded together.
4. The label-position cache returns geometrically identical positions
   on hit but does not return aliased numpy buffers.
"""

from __future__ import annotations

import numpy as np

from crystal_viewer.static_publication import plot_crystal


def _dap4_loaded_crystal():
    from crystal_viewer.loader import build_loaded_crystal

    return build_loaded_crystal(
        name="DAP-4",
        cif_path="scripts/data/DAP-4.cif",
        title="DAP-4",
    )


def test_auto_view_dir_cache_hit_returns_independent_copy():
    """Mutating the returned arrays must not poison the cache."""
    plot_crystal._AUTO_VIEW_CACHE.clear()
    bundle = _dap4_loaded_crystal()

    cache = plot_crystal._AUTO_VIEW_CACHE
    assert len(cache) >= 1, "first build_loaded_crystal should populate the cache"
    cached_view = next(iter(cache.values()))[0].copy()

    bundle2 = _dap4_loaded_crystal()
    view_b = bundle2.scene["view_direction"]

    np.testing.assert_allclose(view_b, cached_view, atol=1e-9)
    view_b[:] = 99.0
    fresh_cached_view = next(iter(cache.values()))[0]
    np.testing.assert_allclose(fresh_cached_view, cached_view, atol=1e-9)


def test_auto_view_dir_cache_does_not_collide_across_structures():
    """Different CIFs must not silently share a cache entry."""
    from crystal_viewer.loader import build_loaded_crystal

    plot_crystal._AUTO_VIEW_CACHE.clear()
    a = build_loaded_crystal(name="DAP-4", cif_path="scripts/data/DAP-4.cif", title="DAP-4")
    b = build_loaded_crystal(name="SY", cif_path="scripts/data/SY.cif", title="SY")

    assert len(plot_crystal._AUTO_VIEW_CACHE) >= 2
    assert not np.allclose(a.scene["view_direction"], b.scene["view_direction"]), (
        "two structurally distinct CIFs collided on the same auto_view_dir cache key"
    )


def test_label_position_cache_returns_independent_copies():
    """The cached positions must round-trip but the caller must be free
    to mutate the returned list (callers in the renderer do exactly
    that)."""
    plot_crystal._LABEL_POS_CACHE.clear()
    bundle = _dap4_loaded_crystal()
    draw_atoms = bundle.scene["draw_atoms"]
    label_atoms = draw_atoms[:24]
    view_x = np.array([1.0, 0.0, 0.0])
    view_y = np.array([0.0, 1.0, 0.0])

    out1 = plot_crystal._compute_label_positions(
        label_atoms, view_x, view_y, all_atoms=draw_atoms
    )
    out2 = plot_crystal._compute_label_positions(
        label_atoms, view_x, view_y, all_atoms=draw_atoms
    )
    assert len(out1) == len(out2)
    for a, b in zip(out1, out2):
        np.testing.assert_allclose(a, b, atol=1e-9)
        assert a is not b, "cache returned aliased numpy buffer (mutations would leak)"
