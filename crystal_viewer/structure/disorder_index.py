"""Coordinate bridge between MolCrysKit disorder indices and raw atoms.

MolCrysKit's ``generate_ordered_replicas_from_disordered_sites`` returns
``kept_indices`` that index into the ``DisorderInfo`` arrays produced by
:func:`molcrys_kit.io.cif.scan_cif_disorder`. MatterVis builds its own
``raw_atoms`` list with an independent CIF parse + symmetry expansion
(:func:`crystal_viewer.structure.cif_parse.parse_asu`). The two index spaces
are **not** guaranteed to line up: on DAN-2, ``scan_cif_disorder`` yields 1249
sites while ``parse_asu`` yields 1081, and the orderings diverge partway
through. Treating ``kept_indices`` as direct ``raw_atoms`` positions therefore
silently mis-selects the major orientation (the "selected replica is not MCK's
optimal" bug).

Both expansions live in the same lattice, so the stable bridge is geometric:
match a ``DisorderInfo`` site to the ``raw_atoms`` entry with the same element
whose wrapped fractional coordinate coincides (matches are exact to numerical
precision in practice). This module is the single source of that mapping; the
loader (for ``_is_minor`` tagging) and the disorder-resolve operation (for
hover highlighting) both consume it so they agree on which atoms a replica
selects.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable

import numpy as np

# Squared fractional-distance tolerance. Real matches are ~0 (the two parsers
# apply the same symmetry math), so a tight tolerance avoids snapping a major
# site onto its nearby minor alternate.
_MATCH_TOL = 0.03
_MATCH_TOL_SQ = _MATCH_TOL * _MATCH_TOL


def _element(atom: dict[str, Any]) -> str:
    for key in ("elem", "element", "symbol"):
        value = atom.get(key)
        if value:
            return str(value)
    match = re.match(r"([A-Za-z]+)", str(atom.get("label", "")))
    return match.group(1) if match else ""


def _wrapped(frac: Any) -> np.ndarray | None:
    if frac is None:
        return None
    arr = np.asarray(frac, dtype=float)
    if arr.shape != (3,) or not np.all(np.isfinite(arr)):
        return None
    return arr - np.floor(arr)


@lru_cache(maxsize=16)
def _scan_disorder_info(cif_path: str) -> tuple[tuple[float, ...], ...] | None:
    """Return ``DisorderInfo`` ``(symbol, fx, fy, fz)`` rows, cached per CIF."""
    try:
        from molcrys_kit.io.cif import scan_cif_disorder

        info = scan_cif_disorder(cif_path)
        frac = np.asarray(info.frac_coords, dtype=float)
        symbols = list(info.symbols)
    except Exception:
        return None
    rows: list[tuple[float, ...]] = []
    for idx in range(len(frac)):
        sym = str(symbols[idx]) if idx < len(symbols) else ""
        rows.append((sym, float(frac[idx][0]), float(frac[idx][1]), float(frac[idx][2])))
    return tuple(rows)


def map_mck_indices_to_raw(
    cif_path: str,
    raw_atoms: list[dict[str, Any]],
    mck_indices: Iterable[int],
) -> dict[int, int]:
    """Map MolCrysKit ``DisorderInfo`` indices to ``raw_atoms`` indices.

    Matching is by ``(element, wrapped fractional coordinate)``; an index is
    omitted from the result when no same-element ``raw_atoms`` entry lies
    within :data:`_MATCH_TOL` of it. The mapping is many-to-one safe: each MCK
    index resolves to its single nearest raw atom.
    """
    rows = _scan_disorder_info(str(cif_path))
    if not rows:
        return {}

    by_element: dict[str, list[tuple[int, np.ndarray]]] = {}
    for raw_idx, atom in enumerate(raw_atoms):
        if not isinstance(atom, dict):
            continue
        wrapped = _wrapped(atom.get("frac"))
        if wrapped is None:
            continue
        by_element.setdefault(_element(atom), []).append((raw_idx, wrapped))

    out: dict[int, int] = {}
    for mck_idx in {int(i) for i in mck_indices}:
        if mck_idx < 0 or mck_idx >= len(rows):
            continue
        sym, fx, fy, fz = rows[mck_idx]
        target = np.array([fx, fy, fz], dtype=float)
        target -= np.floor(target)
        best_raw: int | None = None
        best_dist = float("inf")
        for raw_idx, wrapped in by_element.get(sym, ()):  # same-element only
            delta = target - wrapped
            delta -= np.round(delta)
            dist = float(np.dot(delta, delta))
            if dist < best_dist:
                best_dist = dist
                best_raw = raw_idx
        if best_raw is not None and best_dist <= _MATCH_TOL_SQ:
            out[mck_idx] = best_raw
    return out
