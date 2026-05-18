"""Operation adapters split by source-vs-display ownership.

`ops/source` adapters consume and return real source-side crystal objects and
re-enter rendering through the loader. `ops/display` adapters consume and
return rendered scene dicts for cheap visual augmentation. Legacy transform
helpers remain re-exported here for compatibility.
"""
from __future__ import annotations

from crystal_viewer.transforms import *  # noqa: F401,F403
