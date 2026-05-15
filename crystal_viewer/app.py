from __future__ import annotations

# Thin public entrypoint. Keep the old ``crystal_viewer.app`` symbol surface
# intact while the implementation lives in ``dash_app_impl``.
from . import dash_app_impl as _dash_app_impl

globals().update({
    name: getattr(_dash_app_impl, name)
    for name in dir(_dash_app_impl)
    if not name.startswith("__")
})

__all__ = [name for name in globals() if not name.startswith("__")]


if __name__ == "__main__":
    _dash_app_impl.main()
