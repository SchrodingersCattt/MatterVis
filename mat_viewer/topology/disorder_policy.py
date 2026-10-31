"""Disorder policy for coordination shells.

``identify_molecules`` defaults to ``as_refined``, so loading keeps every
refined site. ``find_polyhedra`` defaults to ``major`` and accepts the same
``disorder_policy`` values:

* ``major`` (default) keeps the highest-occupancy component of each
  mutually exclusive family.
* ``exclusive`` builds one shell per orientation.
* ``as_refined`` keeps every refined site. The centre query uses that fused
  realization, so a shell is drawn when the ligands still form one.

MatterVis does not invent ligand coordinates when the selected policy still
has no drawable hull. The centre moiety sent to ``find_polyhedra`` is the
Hill formula of each realization from MolCrysKit's public disorder API,
restricted to the source molecule index. The displayed fragment formula
selects the centre and is not used as that moiety string.
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


def center_moiety_formulas(molecule: Any, policy: str) -> list[str] | None:
    """Hill formulas of every realization ``policy`` keeps on ``molecule``.

    ``None`` means the molecule cannot report chemical symbols, so the
    caller keeps the as-loaded signature. Each distinct realization is
    returned. The first mask is not treated as the only centre.
    """

    if not hasattr(molecule, "get_chemical_symbols"):
        return None
    from molcrys_kit.analysis.disorder import disorder_realizations

    try:
        realizations = disorder_realizations(
            molecule, normalize_disorder_policy(policy)
        )
    except (TypeError, ValueError, AttributeError):
        return None
    symbols = [str(symbol) for symbol in molecule.get_chemical_symbols()]
    formulas: list[str] = []
    for realization in realizations or []:
        mask = getattr(realization, "mask", None)
        if mask is None:
            continue
        flags = [bool(keep) for keep in mask]
        if len(flags) != len(symbols):
            continue
        kept = [symbol for symbol, keep in zip(symbols, flags) if keep]
        if not kept:
            continue
        formula = _hill_formula(kept)
        if formula and formula not in formulas:
            formulas.append(formula)
    return formulas or None


def _hill_formula(symbols: list[str]) -> str:
    from ase import Atoms

    return str(Atoms(symbols=symbols).get_chemical_formula(mode="hill", empirical=False))


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
