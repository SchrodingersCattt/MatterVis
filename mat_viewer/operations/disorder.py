from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable


_VALID_METHODS = {"optimal", "enumerate", "random"}


def _coerce_method(method: Any) -> str:
    value = str(method or "enumerate").strip().lower()
    if value not in _VALID_METHODS:
        raise ValueError("method must be one of: optimal, enumerate, random")
    return value


def _coerce_count(count: Any, *, method: str) -> int:
    if method == "optimal":
        return 1
    try:
        value = int(count)
    except (TypeError, ValueError):
        value = 5
    return max(1, min(value, 128))


def _coerce_seed(seed: Any) -> int | None:
    if seed in (None, ""):
        return None
    try:
        return int(seed)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=32)
def _mck_replicas(
    cif_path: str,
    *,
    method: str,
    count: int,
    seed: int | None,
) -> tuple[tuple[int, ...], ...]:
    from molcrys_kit.analysis.disorder import (
        generate_ordered_replicas_from_disordered_sites,
    )

    replicas = generate_ordered_replicas_from_disordered_sites(
        cif_path,
        generate_count=count,
        method=method,
        random_seed=seed,
        return_kept_indices=True,
    )
    out: list[tuple[int, ...]] = []
    for item in replicas or []:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        _crystal, kept_indices = item
        try:
            kept = tuple(sorted({int(idx) for idx in kept_indices}))
        except (TypeError, ValueError):
            continue
        out.append(kept)
    return tuple(out)


def _disorder_raw_indices(raw_atoms: list[dict[str, Any]]) -> set[int]:
    """Raw-atom indices that participate in occupancy disorder (occ < 1).

    MCK's ``kept_indices`` index directly into MatterVis ``raw_atoms``
    (the loader's ``_tag_shelx_occupancy_disorder`` relies on exactly this
    1:1 correspondence to mirror the optimal selection onto ``_is_minor``),
    so disorder membership is a plain occupancy test on ``raw_atoms``.
    """
    out: set[int] = set()
    for idx, atom in enumerate(raw_atoms):
        if not isinstance(atom, dict):
            continue
        try:
            occ = float(atom.get("occ", 1.0))
        except (TypeError, ValueError):
            occ = 1.0
        if occ < 0.999:
            out.add(idx)
    return out


def resolve_disorder(
    bundle: Any,
    *,
    method: str,
    count: int,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Return ordered-replica summaries for a loaded CIF bundle.

    Each summary carries ``highlight_indices`` -- the ``raw_atoms`` indices
    this replica selects differently from the optimal solution (the
    alternative orientation it chose), falling back to the disorder-
    involved sites it keeps when it *is* the optimal one. MCK's
    ``kept_indices`` index directly into MatterVis ``raw_atoms`` (same
    bridge the loader uses for ``_is_minor``), and ``draw_atoms`` carry a
    ``_source_index`` back to ``raw_atoms``, so the renderer maps these by
    index -- the proven, label/coordinate-independent path.
    """
    cif_path = str(getattr(bundle, "cif_path", "") or "")
    if not cif_path:
        return []

    method = _coerce_method(method)
    count = _coerce_count(count, method=method)
    seed = _coerce_seed(seed)

    try:
        optimal_sets = _mck_replicas(cif_path, method="optimal", count=1, seed=None)
        replica_sets = _mck_replicas(cif_path, method=method, count=count, seed=seed)
    except Exception:
        return []

    if not replica_sets:
        return []

    raw_atoms = list(getattr(bundle, "raw_atoms", []) or [])
    disorder_sites = _disorder_raw_indices(raw_atoms)

    # MCK kept indices live in ``scan_cif_disorder`` space, which does not line
    # up with MatterVis ``raw_atoms`` (DAN-2: 1249 vs 1081). Bridge every index
    # we touch (optimal + all replicas) to raw-atom positions by coordinate so
    # the diff and the highlight are computed in the same space the renderer
    # resolves via ``_source_index``.
    from ..structure.disorder_index import map_mck_indices_to_raw

    all_mck_indices: set[int] = set()
    for kept_tuple in (*optimal_sets, *replica_sets):
        all_mck_indices.update(int(i) for i in kept_tuple)
    idx_map = map_mck_indices_to_raw(cif_path, raw_atoms, all_mck_indices)

    def _to_raw(indices: Iterable[int]) -> set[int]:
        return {idx_map[int(i)] for i in indices if int(i) in idx_map}

    optimal = _to_raw(optimal_sets[0]) if optimal_sets else _to_raw(replica_sets[0])

    out: list[dict[str, Any]] = []
    seen: set[tuple[int, ...]] = set()
    for index, kept_tuple in enumerate(replica_sets, start=1):
        if kept_tuple in seen:
            continue
        seen.add(kept_tuple)
        kept = _to_raw(kept_tuple)
        added = sorted(kept - optimal)
        dropped = sorted(optimal - kept)
        if added:
            # The alternative-orientation atoms this replica selected.
            highlight = added
        else:
            # This replica equals the optimal solution; highlight the
            # disorder-involved sites it keeps so hover still shows what
            # orientation it selected.
            highlight = sorted(kept & disorder_sites)
        out.append(
            {
                "id": f"{method}-{index}",
                "label": f"{method.capitalize()} {index}",
                "method": method,
                "index": index,
                "kept_indices": list(kept_tuple),
                "kept_count": len(kept_tuple),
                "highlight_indices": highlight,
                "added_indices": added,
                "dropped_indices": dropped,
            }
        )
    return out
