from __future__ import annotations

from . import core as _core
globals().update({
    name: getattr(_core, name)
    for name in dir(_core)
    if not name.startswith("__")
})

from . import uploads as _uploads
globals().update({
    name: getattr(_uploads, name)
    for name in dir(_uploads)
    if not name.startswith("__")
})

__all__ = [name for name in globals() if not name.startswith("__")]
