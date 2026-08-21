from __future__ import annotations

from ..structure import bonds as _impl
globals().update({
    name: getattr(_impl, name)
    for name in getattr(_impl, "__all__", ())
    if hasattr(_impl, name)
})

__all__ = list(getattr(_impl, "__all__", ()))
