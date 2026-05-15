from __future__ import annotations

# Transitional helper facade. Concrete helpers still live in dash_app_impl
# until the callback split is completed; import this module as the stable home.
from .dash_app_impl import *  # noqa: F401,F403
