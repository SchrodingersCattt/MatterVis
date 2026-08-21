"""Deterministic molecule images derived from public MCK bond records."""

from __future__ import annotations

from typing import Any

import numpy as np


def unwrap_from_bond_records(
    atoms: list[dict[str, Any]],
    matrix: Any,
    analysis: Any,
) -> list[dict[str, Any]]:
    """Return continuous finite molecules without trusting traversal images.

    MolCrysKit's signed ``BondRecord.right_image_shift`` is the invariant:
    ``left@q`` is connected to ``right@(q + shift)``.  Reconstructing integer
    image potentials from those records makes the selected molecule image
    independent of ASE/NumPy neighbour traversal order.
    """
    out = [dict(atom) for atom in atoms]
    for index, atom in enumerate(out):
        atom["_unwrapped"] = False
        atom["_source_index"] = index

    matrix_array = np.asarray(matrix, dtype=float)
    records = list(analysis.bond_records)
    for molecule_index, members in enumerate(analysis.mol_indices):
        member_set = {int(value) for value in members}
        adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {
            index: [] for index in member_set
        }
        for record in records:
            left = int(record["left"])
            right = int(record["right"])
            if left not in member_set or right not in member_set:
                continue
            shift = tuple(int(value) for value in record["right_image_shift"])
            adjacency[left].append((right, shift))
            adjacency[right].append((left, tuple(-value for value in shift)))
        for neighbours in adjacency.values():
            neighbours.sort(key=lambda item: (item[0], item[1]))

        potentials: dict[int, tuple[int, int, int]] = {}
        inconsistent = False
        for anchor in sorted(member_set):
            if anchor in potentials:
                continue
            potentials[anchor] = (0, 0, 0)
            queue = [anchor]
            while queue:
                current = queue.pop(0)
                current_shift = potentials[current]
                for neighbour, edge_shift in adjacency[current]:
                    proposed = tuple(
                        current_shift[axis] + edge_shift[axis]
                        for axis in range(3)
                    )
                    known = potentials.get(neighbour)
                    if known is None:
                        potentials[neighbour] = proposed
                        queue.append(neighbour)
                    elif known != proposed:
                        inconsistent = True

        continuous_frac: dict[int, np.ndarray] = {}
        for index in sorted(member_set):
            if not 0 <= index < len(out):
                continue
            wrapped = np.asarray(out[index].get("frac"), dtype=float)
            if wrapped.shape != (3,) or not np.all(np.isfinite(wrapped)):
                continue
            continuous_frac[index] = wrapped + np.asarray(
                potentials.get(index, (0, 0, 0)), dtype=float
            )

        # A non-zero cycle translation is a periodic network, not a finite
        # molecule image.  The span guard retains the established framework
        # policy for acyclic representations of extended structures.
        coords = np.asarray(list(continuous_frac.values()), dtype=float)
        spans_cell = bool(
            len(coords) > 1 and np.any(np.ptp(coords, axis=0) > 0.9)
        )
        if inconsistent or spans_cell:
            continue

        for index, frac in continuous_frac.items():
            out[index]["_wrapped_frac"] = np.asarray(
                out[index].get("frac"), dtype=float
            ).copy()
            out[index]["_source_molecule_index"] = molecule_index
            out[index]["frac"] = frac
            out[index]["cart"] = frac @ matrix_array
            out[index]["_unwrapped"] = True
    return out


__all__ = ["unwrap_from_bond_records"]
