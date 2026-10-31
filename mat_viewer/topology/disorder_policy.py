"""Disorder policy for coordination shells.

``identify_molecules`` defaults to ``as_refined``, so loading keeps every
refined site. ``find_polyhedra`` defaults to ``major`` and accepts the same
``disorder_policy`` values:

* ``major`` (default) keeps the highest-occupancy component of each
  mutually exclusive family.
* ``exclusive`` builds one shell per orientation.
* ``as_refined`` keeps every refined site. A fused pair of orientations can
  then fail to match a real moiety, and the shell stays empty.

MatterVis does not invent ligand coordinates when the selected policy still
has no drawable hull.
"""

from __future__ import annotations

import inspect
from typing import Any

DISORDER_POLICIES = ("major", "exclusive", "as_refined")
DEFAULT_DISORDER_POLICY = "major"


class PolyhedronShellError(ValueError):
    """A requested polyhedron has no drawable shell under the chosen policy."""

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = {
            "schema": "mattervis.polyhedron-shell-error/v1",
            "message": message,
            **details,
        }

    def to_dict(self) -> dict[str, Any]:
        return dict(self.details)


def normalize_disorder_policy(
    value: Any, *, default: str = DEFAULT_DISORDER_POLICY
) -> str:
    """Return ``major``, ``exclusive``, or ``as_refined``."""

    if value is None or value == "":
        value = default
    text = str(value).strip().lower().replace("-", "_")
    if text not in DISORDER_POLICIES:
        allowed = ", ".join(DISORDER_POLICIES)
        raise ValueError(
            f"disorder_policy must be one of {allowed}; got {value!r}. "
            "'major' keeps the highest-occupancy component. "
            "'exclusive' keeps one shell per orientation. "
            "'as_refined' keeps every refined site."
        )
    return text


def _accepts_disorder_policy(func: Any) -> bool:
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    if "disorder_policy" in parameters:
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def invoke_find_polyhedra(
    func: Any,
    crystal: Any,
    central: str,
    ligand: str,
    *,
    level: str,
    disorder_policy: str = DEFAULT_DISORDER_POLICY,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Call ``find_polyhedra`` and forward ``disorder_policy`` when supported."""

    policy = normalize_disorder_policy(disorder_policy)
    if _accepts_disorder_policy(func):
        kwargs["disorder_policy"] = policy
    records = func(crystal, central, ligand, level=level, **kwargs)
    if isinstance(records, tuple):
        records = records[0]
    return list(records or [])
