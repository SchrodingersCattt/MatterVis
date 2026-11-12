"""Phone-facing Analysis, Display, and Operate snapshots.

Public facade; the implementation lives in :mod:`mat_viewer.phone.snapshot`.
"""

from __future__ import annotations

from mat_viewer.phone.snapshot import (
    analyze_cif,
    display,
    operate_commit_major,
    operate_commit_major_with_cif,
)

__all__ = [
    "analyze_cif",
    "display",
    "operate_commit_major",
    "operate_commit_major_with_cif",
]
