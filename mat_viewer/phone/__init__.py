"""Phone-facing Analysis, Display, and Operate snapshots.

Public facade; the implementation lives in :mod:`mat_viewer.phone.snapshot`
and :mod:`mat_viewer.phone.candidates`.
"""

from __future__ import annotations

from mat_viewer.phone.candidates import (
    CandidatePayloadError,
    CandidateRequestError,
    generate_disorder_candidates,
)
from mat_viewer.phone.snapshot import (
    analyze_cif,
    display,
    operate_commit_major,
    operate_commit_major_with_cif,
)

__all__ = [
    "CandidatePayloadError",
    "CandidateRequestError",
    "analyze_cif",
    "display",
    "generate_disorder_candidates",
    "operate_commit_major",
    "operate_commit_major_with_cif",
]
