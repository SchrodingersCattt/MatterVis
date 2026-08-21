from __future__ import annotations

from . import core as _core
from . import state as _state
from . import store as _store

# Re-export only explicit public contracts.  ``dir(core)`` previously leaked
# imported local chemistry helpers (including ``find_bonds``) through this
# facade, making a private fallback look like a supported scene API.
globals().update({
    name: getattr(_core, name)
    for name in getattr(_core, "__all__", ())
    if hasattr(_core, name)
})

# state.py — narrow, uses explicit __all__
globals().update({
    name: getattr(_state, name)
    for name in getattr(_state, "__all__", [])
    if hasattr(_state, name)
})

# store.py — thin facade over mat_viewer.scenes, uses explicit __all__
globals().update({
    name: getattr(_store, name)
    for name in getattr(_store, "__all__", [])
    if hasattr(_store, name)
})

__all__ = [
    *getattr(_core, "__all__", ()),
    *getattr(_state, "__all__", ()),
    *getattr(_store, "__all__", ()),
]
