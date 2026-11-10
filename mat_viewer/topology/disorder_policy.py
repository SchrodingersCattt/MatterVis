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
Hill formula of each realization from MolCrysKit's public
``disorder_realizations`` API, restricted to the source molecule index.
The displayed fragment formula selects the centre and is not used as that
moiety string.

Published ``molcrys-kit>=0.8.0,<0.9.0`` does not export
``disorder_realizations``. That ``ImportError`` falls back to the as-loaded
molecule formula, so ordered shells still render. Split orientations require
the development pin. Other realization failures are logged and use the same
fallback.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

logger = logging.getLogger(__name__)

_missing_realization_api = False

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


def _load_disorder_realizations():
    """Return MolCrysKit's public realization helper, or ``None`` on 0.8.x.

    The numeric requirement stays ``molcrys-kit>=0.8.0,<0.9.0``. Releases in
    that window predate ``disorder_realizations``, so a missing export is the
    compatibility path rather than a hard failure.
    """

    global _missing_realization_api
    try:
        from molcrys_kit.analysis.disorder import disorder_realizations
    except ImportError as exc:
        if not _missing_realization_api:
            _missing_realization_api = True
            logger.warning(
                "Installed MolCrysKit has no disorder_realizations; "
                "molecule-level polyhedra use the as-loaded formula. "
                "Split orientations need the MatterVis development pin. %s",
                exc,
            )
        return None
    return disorder_realizations


def center_moiety_formulas(molecule: Any, policy: str) -> list[str] | None:
    """Hill formulas of every realization ``policy`` keeps on ``molecule``.

    ``None`` means the caller keeps the as-loaded signature. That happens
    when the molecule cannot report chemical symbols, when this MolCrysKit
    build has no realization API, or when that API rejects the molecule.
    Each distinct realization is returned. The first mask is not treated as
    the only centre.
    """

    if not hasattr(molecule, "get_chemical_symbols"):
        return None
    disorder_realizations = _load_disorder_realizations()
    if disorder_realizations is None:
        return None
    try:
        realizations = disorder_realizations(
            molecule, normalize_disorder_policy(policy)
        )
    except (TypeError, ValueError, AttributeError):
        logger.warning(
            "MolCrysKit disorder realizations failed for policy %s; "
            "using the as-loaded molecule formula.",
            policy,
            exc_info=True,
        )
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
