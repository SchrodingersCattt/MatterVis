from __future__ import annotations

from . import core as _core
from . import state as _state
from . import store as _store

# Re-export public symbols from each sub-module.
# Legacy: build_scene_from_atoms is defined in scene/core.py but the
# canonical owner is render/assembly.py per agents/scene_api.md.
#
# core.py is the broad compatibility facade — use dir() because its
# public surface includes legacy static-publication imports and other
# transient symbols.

# core.py
globals().update({
    name: getattr(_core, name)
    for name in dir(_core)
    if not name.startswith("__")
})

# state.py — narrow, uses explicit __all__
globals().update({
    name: getattr(_state, name)
    for name in getattr(_state, "__all__", [])
    if hasattr(_state, name)
})

# store.py — thin facade over crystal_viewer.scenes, uses explicit __all__
globals().update({
    name: getattr(_store, name)
    for name in getattr(_store, "__all__", [])
    if hasattr(_store, name)
})

__all__ = [name for name in globals() if not name.startswith("__")]
