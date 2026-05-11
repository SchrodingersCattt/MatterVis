"""Compatibility shim for ``molcrys_kit`` symbols that vary across
versions.

Currently the only symbol we need is ``unwrap_positions_along_bonds``,
which existed in older ``molcrys_kit`` releases but was removed in
0.2.x. Both :mod:`crystal_viewer.loader` and
:mod:`crystal_viewer.molcrys_bridge` import it through this module so a
missing upstream symbol falls back to a small vendored implementation
instead of breaking the whole viewer.

The algorithm is straightforward: given a bond graph whose edges carry
a precomputed minimum-image bond vector under the ``vector`` attribute
(set by callers via ``pc._nearest_pbc_cart``), BFS the connected
component starting from any node and propagate
``new_neighbour_pos = parent_pos + edge_vector``. The result is a
PBC-continuous (un-wrapped) cartesian position per component atom.
This matches the original ``molcrys_kit`` semantics used by
:func:`crystal_viewer.loader._unwrap_atom_pool`.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Optional

import numpy as np

try:
    from molcrys_kit.utils.geometry import (  # type: ignore
        unwrap_positions_along_bonds as _upstream_unwrap,
    )
except ImportError:  # pragma: no cover - depends on installed molcrys_kit
    _upstream_unwrap = None


def _vendored_unwrap(
    graph: Any,
    indices: Iterable[int],
    positions: np.ndarray,
    *,
    max_atoms: Optional[int] = None,
) -> tuple[np.ndarray, list[int]]:
    """BFS-based PBC unwrap.

    ``graph`` must be a ``networkx.Graph`` whose edges carry a
    ``vector`` attribute equal to the minimum-image bond vector
    ``pos[j_image] - pos[i]`` (sign-aware: ``vector`` from i->j is the
    inverse of the j->i edge if traversed back).

    Returns ``(unwrapped_positions, completed_indices)`` where
    ``unwrapped_positions`` is a ``(len(indices), 3)`` ndarray in the
    same order as ``indices``, and ``completed_indices`` is the subset
    of ``indices`` actually visited (empty if the BFS gave up because
    of ``max_atoms``).
    """
    indices = list(indices)
    component_set = set(indices)
    pos = np.asarray(positions, dtype=float)
    out = pos.copy()
    if not indices:
        return out[indices].reshape(0, 3), []

    seed = indices[0]
    visited: dict[int, np.ndarray] = {seed: pos[seed].copy()}
    queue: deque[int] = deque([seed])
    while queue:
        current = queue.popleft()
        if max_atoms is not None and len(visited) > int(max_atoms):
            return np.zeros((0, 3)), []
        for neighbour in graph.neighbors(current):
            if neighbour not in component_set or neighbour in visited:
                continue
            data = graph.get_edge_data(current, neighbour) or {}
            vec = data.get("vector")
            if vec is None:
                vec = pos[neighbour] - pos[current]
            else:
                vec = np.asarray(vec, dtype=float)
                # Edges are stored with a canonical orientation
                # (the loader writes ``vector = near - start if i < j
                # else start - near``). When traversing in the
                # opposite direction the sign flips.
                if neighbour < current:
                    vec = -vec
            visited[neighbour] = visited[current] + vec
            queue.append(neighbour)

    if len(visited) != len(component_set):
        return np.zeros((0, 3)), []

    out_arr = np.array([visited[idx] for idx in indices], dtype=float)
    return out_arr, list(indices)


def unwrap_positions_along_bonds(
    graph: Any,
    indices: Iterable[int],
    positions: np.ndarray,
    *,
    max_atoms: Optional[int] = None,
) -> tuple[np.ndarray, list[int]]:
    """Public wrapper -- delegates to upstream when available, vendored
    fallback otherwise. Both branches share the same return contract."""
    if _upstream_unwrap is not None:
        return _upstream_unwrap(graph, indices, positions, max_atoms=max_atoms)
    return _vendored_unwrap(graph, indices, positions, max_atoms=max_atoms)


__all__ = ["unwrap_positions_along_bonds"]
