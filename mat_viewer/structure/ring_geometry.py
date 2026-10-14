"""Fast local ring geometry for large molecular framework graphs."""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx
import numpy as np

# Exact minimum-cycle-basis is retained for ordinary molecules. Framework
# graphs switch to this bounded path before NetworkX's dense-graph algorithm
# can dominate upload time.
RING_MCB_NODE_LIMIT = 96
RING_MCB_EDGE_LIMIT = 160
MAX_RENDER_RING_SIZE = 8


def bounded_ring_geometries(molecule) -> list[SimpleNamespace]:
    """Build small ring records without ``minimum_cycle_basis``.

    A fundamental cycle basis is linear in graph size. Filtering it to the
    small rings used by the renderer avoids periodic macrocycles while keeping
    local chemical ring geometry and aromaticity metadata.
    """
    graph = getattr(molecule, "graph", None)
    if graph is None or graph.number_of_edges() == 0:
        return []
    try:
        cycles = nx.cycle_basis(graph)
    except (nx.NetworkXError, TypeError, ValueError):
        return []
    positions = np.asarray(molecule.get_positions(), dtype=float)
    symbols = tuple(str(value) for value in molecule.get_chemical_symbols())
    seen: set[frozenset[int]] = set()
    rings: list[SimpleNamespace] = []
    for raw_cycle in cycles:
        cycle = tuple(int(index) for index in raw_cycle)
        if not 3 <= len(cycle) <= MAX_RENDER_RING_SIZE:
            continue
        key = frozenset(cycle)
        if key in seen:
            continue
        seen.add(key)
        points = positions[list(cycle)]
        centroid = points.mean(axis=0)
        _, singular_values, vh = np.linalg.svd(points - centroid, full_matrices=False)
        normal = np.asarray(vh[-1], dtype=float)
        rmsd = float(singular_values[-1] / np.sqrt(len(points)))
        planar = bool(rmsd <= 0.25)
        aromatic = False
        if len(cycle) in (5, 6, 7) and planar and all(
            symbols[index] in {"C", "N", "O"} for index in cycle
        ):
            edge_lengths = [
                float(
                    np.linalg.norm(
                        positions[cycle[i]]
                        - positions[cycle[(i + 1) % len(cycle)]]
                    )
                )
                for i in range(len(cycle))
            ]
            aromatic = all(1.20 <= length <= 1.45 for length in edge_lengths)
        rings.append(
            SimpleNamespace(
                atom_indices=tuple(sorted(cycle)),
                cycle_atom_indices=cycle,
                symbols=tuple(symbols[index] for index in sorted(cycle)),
                centroid_A=tuple(float(value) for value in centroid),
                normal=tuple(float(value) for value in normal),
                plane_rmsd_A=rmsd,
                is_planar=planar,
                is_aromatic=aromatic,
                size=len(cycle),
            )
        )
    return rings


__all__ = [
    "MAX_RENDER_RING_SIZE",
    "RING_MCB_EDGE_LIMIT",
    "RING_MCB_NODE_LIMIT",
    "bounded_ring_geometries",
]
