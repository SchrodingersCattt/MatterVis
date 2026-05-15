from __future__ import annotations

# Layout construction is currently embedded in create_app in dash_app_impl.
# This module is the stable target for the follow-up extraction.
from .dash_app_impl import create_app

__all__ = ["create_app"]
