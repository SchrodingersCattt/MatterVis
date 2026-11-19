"""Byte and entry bounds for in-process render caches.

A long-lived web process used to retain Mesh3d payloads by entry count
only. Sixteen figure-JSON copies plus a 32-deep broadcast journal, an
unbounded per-scene mesh cache, and unbounded scene/transform maps can
pin hundreds of megabytes once a supercell or a dense ellipsoid mesh is
involved. The caps below keep the hot entry for the current style (so a
repeat view does not rebuild) and evict the least-recently-used entries
until both the entry count and the byte budget hold.

A single newest payload is kept even when it alone exceeds its byte
budget. Evicting that entry would force every repeat of the same style
to rebuild, which is the regression the figure cache exists to prevent.
"""

from __future__ import annotations

import sys
import threading
import weakref
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

# Per-cache caps. Normal molecules stay under the entry cap; a supercell
# mesh hits the byte cap first and keeps only the newest variants.
MESH_CACHE_MAX_ENTRIES = 8
MESH_CACHE_MAX_BYTES = 32 * 1024 * 1024
LABEL_CACHE_MAX_ENTRIES = 8
LABEL_CACHE_MAX_BYTES = 4 * 1024 * 1024
PAINTER_CACHE_MAX_ENTRIES = 4
PAINTER_CACHE_MAX_BYTES = 24 * 1024 * 1024
# Process-wide pool shared by mesh and polyhedron painter traces.
MESH_POOL_MAX_BYTES = 96 * 1024 * 1024

FIGURE_CACHE_MAX_ENTRIES = 16
FIGURE_CACHE_MAX_BYTES = 128 * 1024 * 1024

FIGURE_JOURNAL_MAX_ENTRIES = 32
FIGURE_JOURNAL_MAX_BYTES = 64 * 1024 * 1024

SCENE_CACHE_MAX_ENTRIES = 8
SCENE_CACHE_MAX_BYTES = 64 * 1024 * 1024
TRANSFORMED_SCENE_CACHE_MAX_ENTRIES = 6
TRANSFORMED_SCENE_CACHE_MAX_BYTES = 96 * 1024 * 1024
FRAGMENT_TABLE_CACHE_MAX_ENTRIES = 8
FRAGMENT_TABLE_CACHE_MAX_BYTES = 32 * 1024 * 1024
ANALYZE_TOPOLOGY_CACHE_MAX_ENTRIES = 512
ANALYZE_TOPOLOGY_CACHE_MAX_BYTES = 48 * 1024 * 1024

# Render-trace maps live on the scene dict. Geometry budgets skip them so
# filling a mesh cache does not evict the scene that owns it.
SCENE_GEOMETRY_SKIP_KEYS = frozenset(
    {"_mesh_trace_cache", "_label_trace_cache"}
)


@dataclass(frozen=True)
class _CacheLimit:
    max_entries: int
    max_bytes: int
    pool: str | None = None


_LIMITS: dict[str, _CacheLimit] = {
    "mesh": _CacheLimit(MESH_CACHE_MAX_ENTRIES, MESH_CACHE_MAX_BYTES, "mesh"),
    "label": _CacheLimit(LABEL_CACHE_MAX_ENTRIES, LABEL_CACHE_MAX_BYTES, None),
    "painter": _CacheLimit(
        PAINTER_CACHE_MAX_ENTRIES, PAINTER_CACHE_MAX_BYTES, "mesh"
    ),
    "figure": _CacheLimit(
        FIGURE_CACHE_MAX_ENTRIES, FIGURE_CACHE_MAX_BYTES, None
    ),
}

_LOCK = threading.Lock()
# Identity refs, not a WeakSet: BudgetedCache compares by contents, so it
# cannot be a WeakSet key without collapsing equal caches together.
_POOLS: dict[str, list[weakref.ref]] = {"mesh": []}
_STAMP = 0


def _tick() -> int:
    global _STAMP
    _STAMP += 1
    return _STAMP


def payload_nbytes(value: Any, *, skip_keys: frozenset[str] | None = None) -> int:
    """Approximate retained bytes of a cache payload.

    Numpy buffers are counted once. Containers are walked so a Plotly
    JSON tree of Python floats is not mistaken for a few dozen pointers.
    """

    seen: set[int] = set()
    if skip_keys and isinstance(value, dict):
        seen.add(id(value))
        total = sys.getsizeof(value)
        for key, item in value.items():
            total += _nbytes(key, seen)
            if key not in skip_keys:
                total += _nbytes(item, seen)
        return total
    return _nbytes(value, seen)


def scene_geometry_nbytes(scene: Any) -> int:
    """Size a manifested scene without its nested trace caches."""

    return payload_nbytes(scene, skip_keys=SCENE_GEOMETRY_SKIP_KEYS)


def _nbytes(value: Any, seen: set[int]) -> int:
    ident = id(value)
    if ident in seen:
        return 0
    seen.add(ident)
    if isinstance(value, np.ndarray):
        return sys.getsizeof(value) + int(value.nbytes)
    if isinstance(value, (bytes, bytearray, memoryview, str)):
        return sys.getsizeof(value)
    if isinstance(value, dict):
        total = sys.getsizeof(value)
        for key, item in value.items():
            total += _nbytes(key, seen) + _nbytes(item, seen)
        return total
    if isinstance(value, (list, tuple)):
        total = sys.getsizeof(value)
        for item in value:
            total += _nbytes(item, seen)
        return total
    if isinstance(value, (set, frozenset)):
        total = sys.getsizeof(value)
        for item in value:
            total += _nbytes(item, seen)
        return total
    return sys.getsizeof(value)


class BudgetedCache(OrderedDict):
    """LRU map that evicts by entry count and by accounted bytes.

    ``remember`` always leaves ``key`` in the map. Older entries are
    dropped until the per-cache cap holds, then until the shared pool
    that this kind belongs to holds. Callers must ``touch`` on a hit so
    the entry stays newer than idle scenes.
    """

    def __init__(self, kind: str):
        if kind not in _LIMITS:
            raise KeyError(f"unknown render cache kind: {kind}")
        super().__init__()
        self.kind = kind
        self._sizes: dict[Any, int] = {}
        self._stamps: dict[Any, int] = {}
        pool = _LIMITS[kind].pool
        if pool is not None:
            with _LOCK:
                _POOLS[pool].append(weakref.ref(self))

    def accounted_bytes(self) -> int:
        return sum(self._sizes.get(key, 0) for key in self)

    def touch(self, key: Any) -> None:
        with _LOCK:
            if key not in self:
                return
            self.move_to_end(key)
            self._stamps[key] = _tick()

    def remember(self, key: Any, value: Any) -> None:
        size = payload_nbytes(value)
        with _LOCK:
            self[key] = value
            self._sizes[key] = size
            self._stamps[key] = _tick()
            self.move_to_end(key)
            self._trim_local_locked(protect=key)
            pool = _LIMITS[self.kind].pool
            if pool is not None:
                _trim_pool_locked(pool, protect=(self, key))

    def pop(self, key: Any, *default: Any) -> Any:
        present = key in self
        if not present:
            if default:
                return default[0]
            raise KeyError(key)
        value = super().pop(key)
        self._forget(key)
        return value

    def popitem(self, last: bool = True) -> tuple[Any, Any]:
        key, value = super().popitem(last=last)
        self._forget(key)
        return key, value

    def clear(self) -> None:
        with _LOCK:
            super().clear()
            self._sizes.clear()
            self._stamps.clear()

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(key)
        self._forget(key)

    def _forget(self, key: Any) -> None:
        self._sizes.pop(key, None)
        self._stamps.pop(key, None)

    def _trim_local_locked(self, *, protect: Any) -> None:
        limit = _LIMITS[self.kind]
        while self._over_limit(limit):
            oldest = next(iter(self), None)
            if oldest is None or oldest == protect:
                return
            self.pop(oldest)

    def _over_limit(self, limit: _CacheLimit) -> bool:
        if len(self) > limit.max_entries:
            return True
        return self.accounted_bytes() > limit.max_bytes and len(self) > 1


def ensure_budgeted_cache(existing: Any, kind: str) -> BudgetedCache:
    """Return a budgeted cache, copying a plain dict in insertion order."""

    if isinstance(existing, BudgetedCache) and existing.kind == kind:
        return existing
    cache = BudgetedCache(kind)
    if isinstance(existing, dict):
        for key, value in list(existing.items()):
            cache.remember(key, value)
    return cache


def retain_scene_render_caches(scene: dict) -> None:
    """Attach the scene's trace caches before a shallow copy.

    ``build_figure`` copies the scene dict before it paints. Creating the
    cache maps on the caller's scene makes that copy share one bounded
    cache instead of allocating a map that dies with the temporary copy.
    """

    if not isinstance(scene, dict):
        return
    scene["_mesh_trace_cache"] = ensure_budgeted_cache(
        scene.get("_mesh_trace_cache"), "mesh"
    )
    scene["_label_trace_cache"] = ensure_budgeted_cache(
        scene.get("_label_trace_cache"), "label"
    )


def mesh_pool_nbytes() -> int:
    with _LOCK:
        return _pool_bytes_locked("mesh")


def _live_caches_locked(pool: str) -> list[BudgetedCache]:
    alive: list[BudgetedCache] = []
    kept: list[weakref.ref] = []
    for ref in _POOLS[pool]:
        cache = ref()
        if cache is not None:
            alive.append(cache)
            kept.append(ref)
    _POOLS[pool][:] = kept
    return alive


def _pool_bytes_locked(pool: str) -> int:
    return sum(cache.accounted_bytes() for cache in _live_caches_locked(pool))


def _trim_pool_locked(pool: str, *, protect: tuple[BudgetedCache, Any]) -> None:
    budget = MESH_POOL_MAX_BYTES if pool == "mesh" else 0
    protected_cache, protected_key = protect
    # Each pass drops one entry. The guard stops a zero-byte ghost key
    # from spinning if it is reported and then already gone.
    for _ in range(100_000):
        if _pool_bytes_locked(pool) <= budget:
            return
        victim = _oldest_in_pool_locked(pool, protect)
        if victim is None:
            return
        cache, key = victim
        if cache is protected_cache and key == protected_key:
            return
        if key not in cache:
            cache._forget(key)
            continue
        cache.pop(key)


def _oldest_in_pool_locked(
    pool: str, protect: tuple[BudgetedCache, Any]
) -> tuple[BudgetedCache, Any] | None:
    protected_cache, protected_key = protect
    oldest: tuple[int, BudgetedCache, Any] | None = None
    for cache in _live_caches_locked(pool):
        for key in list(cache.keys()):
            if cache is protected_cache and key == protected_key:
                continue
            stamp = cache._stamps.get(key, 0)
            if oldest is None or stamp < oldest[0]:
                oldest = (stamp, cache, key)
    if oldest is None:
        return None
    return oldest[1], oldest[2]


def remember_plain_lru(
    cache: dict | None,
    key: Any,
    value: Any,
    sizes: dict[Any, int],
    *,
    max_entries: int,
    max_bytes: int,
    measure: Callable[[Any], int],
) -> OrderedDict:
    """Insert into a scene/transform map and evict the oldest entries."""

    ordered: OrderedDict = (
        cache if isinstance(cache, OrderedDict) else OrderedDict(cache or {})
    )
    ordered[key] = value
    ordered.move_to_end(key)
    sizes[key] = int(measure(value))
    _evict_plain(
        ordered,
        sizes,
        max_entries=max_entries,
        max_bytes=max_bytes,
        protect=key,
        measure=measure,
    )
    return ordered


def touch_plain_lru(cache: dict | None, key: Any) -> None:
    if isinstance(cache, OrderedDict) and key in cache:
        cache.move_to_end(key)


def _evict_plain(
    cache: OrderedDict,
    sizes: dict[Any, int],
    *,
    max_entries: int,
    max_bytes: int,
    protect: Any,
    measure: Callable[[Any], int],
) -> None:
    def total() -> int:
        accounted = 0
        for item in cache:
            if item not in sizes:
                sizes[item] = int(measure(cache[item]))
            accounted += sizes[item]
        return accounted

    while len(cache) > max_entries or (total() > max_bytes and len(cache) > 1):
        oldest = next(iter(cache))
        if oldest == protect:
            break
        cache.pop(oldest)
        sizes.pop(oldest, None)
    for stale in [item for item in sizes if item not in cache]:
        sizes.pop(stale, None)


def clear_scene_render_caches(scene: Any) -> None:
    if not isinstance(scene, dict):
        return
    for field in ("_mesh_trace_cache", "_label_trace_cache"):
        cache = scene.get(field)
        if isinstance(cache, dict):
            cache.clear()


def clear_topology_render_caches(geometry: Any) -> None:
    if not isinstance(geometry, dict):
        return
    wrappers = [geometry]
    paint = geometry.get("_paint_wrapper_cache")
    if isinstance(paint, dict):
        wrappers.extend(value for value in paint.values() if isinstance(value, dict))
        paint.clear()
    for wrapper in wrappers:
        nested = wrapper.get("_style_override_wrapper_cache")
        if isinstance(nested, dict):
            for value in nested.values():
                if isinstance(value, dict):
                    _clear_painter_maps(value)
            nested.clear()
        _clear_painter_maps(wrapper)


def _clear_painter_maps(wrapper: dict) -> None:
    for field in ("_background_dict_cache", "_foreground_dict_cache"):
        cache = wrapper.get(field)
        if isinstance(cache, dict):
            cache.clear()


def clear_bundle_render_caches(bundle: Any) -> None:
    """Drop derived meshes, transformed scenes, and topology trace caches.

    The loaded ``bundle.scene`` geometry stays so the structure can be
    drawn again. Trace maps on that scene are cleared.
    """

    scenes = [getattr(bundle, "scene", None)]
    scene_cache = getattr(bundle, "scene_cache", None)
    if isinstance(scene_cache, dict):
        scenes.extend(scene_cache.values())
    transformed = getattr(bundle, "_transformed_scene_cache", None)
    if isinstance(transformed, dict):
        scenes.extend(transformed.values())
        transformed.clear()
    seen: set[int] = set()
    for scene in scenes:
        if isinstance(scene, dict) and id(scene) not in seen:
            seen.add(id(scene))
            clear_scene_render_caches(scene)
    topology = getattr(bundle, "_topology_state_cache", None)
    if isinstance(topology, dict):
        for geometry in list(topology.values()):
            clear_topology_render_caches(geometry)
        topology.clear()
    analyze = getattr(bundle, "_analyze_topology_cache", None)
    if isinstance(analyze, dict):
        analyze.clear()
    for name in (
        "_scene_cache_nbytes",
        "_transformed_scene_cache_nbytes",
        "_fragment_table_cache_nbytes",
        "_analyze_topology_cache_nbytes",
    ):
        sizes = getattr(bundle, name, None)
        if isinstance(sizes, dict):
            sizes.clear()


__all__ = [
    "ANALYZE_TOPOLOGY_CACHE_MAX_BYTES",
    "ANALYZE_TOPOLOGY_CACHE_MAX_ENTRIES",
    "FIGURE_CACHE_MAX_BYTES",
    "FIGURE_CACHE_MAX_ENTRIES",
    "FIGURE_JOURNAL_MAX_BYTES",
    "FIGURE_JOURNAL_MAX_ENTRIES",
    "FRAGMENT_TABLE_CACHE_MAX_BYTES",
    "FRAGMENT_TABLE_CACHE_MAX_ENTRIES",
    "LABEL_CACHE_MAX_BYTES",
    "LABEL_CACHE_MAX_ENTRIES",
    "MESH_CACHE_MAX_BYTES",
    "MESH_CACHE_MAX_ENTRIES",
    "MESH_POOL_MAX_BYTES",
    "PAINTER_CACHE_MAX_BYTES",
    "PAINTER_CACHE_MAX_ENTRIES",
    "SCENE_CACHE_MAX_BYTES",
    "SCENE_CACHE_MAX_ENTRIES",
    "TRANSFORMED_SCENE_CACHE_MAX_BYTES",
    "TRANSFORMED_SCENE_CACHE_MAX_ENTRIES",
    "BudgetedCache",
    "clear_bundle_render_caches",
    "clear_scene_render_caches",
    "clear_topology_render_caches",
    "ensure_budgeted_cache",
    "mesh_pool_nbytes",
    "payload_nbytes",
    "remember_plain_lru",
    "retain_scene_render_caches",
    "scene_geometry_nbytes",
    "touch_plain_lru",
]
