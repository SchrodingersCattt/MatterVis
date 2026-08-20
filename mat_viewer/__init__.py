from __future__ import annotations

__all__ = ["create_app"]

try:
    from ._version import version as __version__
except ImportError:
    # Source checkouts without an installed build backend still import cleanly.
    __version__ = "0.0.0+unknown"


def create_app(*args, **kwargs):
    """Create the Dash app, importing UI dependencies only when requested."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
