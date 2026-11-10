"""Display, Analysis, and Operate effect core.

Importing the package does not import Dash, Plotly, or a GPU backend.
Heavy scientific imports wait until a caller asks for a session or command.
"""

from __future__ import annotations

__all__ = ["IMPLEMENTED_COMMANDS", "EffectSession"]


def __getattr__(name: str):
    if name == "EffectSession":
        from .session import EffectSession

        return EffectSession
    if name == "IMPLEMENTED_COMMANDS":
        from .operations import IMPLEMENTED_COMMANDS

        return IMPLEMENTED_COMMANDS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
