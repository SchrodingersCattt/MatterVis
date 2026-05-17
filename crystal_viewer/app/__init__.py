from __future__ import annotations

# Thin public entrypoint. Keep the old ``crystal_viewer.app`` symbol surface
# intact while the implementation lives in ``app.dash_impl``.
from . import dash_impl as _dash_impl

globals().update({
    name: getattr(_dash_impl, name)
    for name in dir(_dash_impl)
    if not name.startswith("__")
})

__all__ = [name for name in globals() if not name.startswith("__")]


if __name__ == "__main__":
    _dash_impl.main()
