from __future__ import annotations

__all__ = [
    "AtomPropertyColorSpec",
    "capabilities",
    "create_app",
    "load_structure",
    "prepare_render",
    "render",
    "resolve_requirements",
]

from .properties import AtomPropertyColorSpec

try:
    from ._version import version as __version__
except ImportError:
    # Source checkouts without an installed build backend still import cleanly.
    __version__ = "0.0.0+unknown"


def create_app(*args, **kwargs):
    """Create the Dash app, importing UI dependencies only when requested."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)


def load_structure(*args, **kwargs):
    """Load a canonical structure through the lazy agent facade."""
    from .agent import load_structure as _load_structure

    return _load_structure(*args, **kwargs)


def prepare_render(*args, **kwargs):
    """Compile a backend-neutral render plan without loading a frontend."""
    from .agent import prepare_render as _prepare_render

    return _prepare_render(*args, **kwargs)


def render(*args, **kwargs):
    """Render through an explicitly selected backend."""
    from .agent import render as _render

    return _render(*args, **kwargs)


def capabilities():
    """Return the JSON-safe capability registry."""
    from .capabilities import capabilities as _capabilities

    return _capabilities()


def resolve_requirements(*args, **kwargs):
    """Resolve drawing requirements to exact MatterVis extras."""
    from .capabilities import resolve_requirements as _resolve_requirements

    return _resolve_requirements(*args, **kwargs)
