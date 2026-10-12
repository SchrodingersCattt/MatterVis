"""Compatibility shim for direct legacy imports.

The canonical renderer lives in :mod:`mat_viewer.render.style.core`; this
module deliberately delegates to it so file based integrations cannot drift
to a second palette implementation.
"""

from mat_viewer.render.style.core import *  # noqa: F401,F403
from mat_viewer.render.style.core import __all__ as __all__
