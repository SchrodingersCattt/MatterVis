"""Optional Web application facade.

Importing :mod:`mat_viewer.app` does not import Dash or Plotly.  The historic
symbol surface is resolved from ``dash_impl`` only when a symbol is requested.
"""

from __future__ import annotations

from importlib import import_module


def __getattr__(name: str):
    if name.startswith("__"):
        raise AttributeError(name)
    module = import_module(".dash_impl", __name__)
    try:
        value = getattr(module, name)
    except AttributeError:
        # Preserve ``from mat_viewer.app import backend_core`` style imports.
        try:
            value = import_module(f".{name}", __name__)
        except ModuleNotFoundError as exc:
            raise AttributeError(name) from exc
    globals()[name] = value
    return value


__all__ = ["ApiError", "TopologyUnavailable", "ViewerBackend", "create_app", "main"]
