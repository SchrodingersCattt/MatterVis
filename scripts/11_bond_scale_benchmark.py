#!/usr/bin/env python3
"""Benchmark unified bond candidate generation on synthetic PBC scenes."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crystal_viewer.structure.bonds import find_bonds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atoms", type=int, nargs="+", default=[1000, 5000, 10000])
    args = parser.parse_args()
    lattice = np.eye(3) * 100.0
    for count in args.atoms:
        rng = np.random.default_rng(20260730 + count)
        coords = rng.random((count, 3)) * 100.0
        atoms = [
            {
                "label": f"C{i}", "elem": "C", "cart": coords[i],
                "frac": coords[i] / 100.0, "occ": 1.0, "dg": ".", "da": ".",
                "_bond_partners": (), "_bond_lengths": {}, "_has_bond_table": False,
            }
            for i in range(count)
        ]
        started = time.perf_counter()
        bonds = find_bonds(atoms, M=lattice, cell=None, bond_scale=1.0)
        elapsed = time.perf_counter() - started
        print(f"atoms={count} bonds={len(bonds)} seconds={elapsed:.3f}")


if __name__ == "__main__":
    main()