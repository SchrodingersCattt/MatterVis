from __future__ import annotations

import argparse
import inspect

import numpy as np

from mat_viewer.cli import _build_render_parser
from mat_viewer.loader import build_loaded_crystal
from mat_viewer.render.cli_controls import bond_thresholds_from_args
from mat_viewer.structure.bonds import find_bonds


def _atom(label: str, element: str, x: float) -> dict:
    cart = np.array([x, 0.0, 0.0], dtype=float)
    return {
        "label": label,
        "elem": element,
        "cart": cart,
        "frac": cart / 10.0,
        "occ": 1.0,
        "dg": ".",
        "da": ".",
        "_bond_partners": (),
        "_bond_lengths": {},
        "_has_bond_table": False,
    }


def test_issue_58_python_and_cli_surfaces_expose_pair_cutoffs() -> None:
    """Keep the caller-facing bond policy contract from regressing.

    Issue #58 was originally reported because connectivity cutoffs were only
    hard-coded internals. The public loader, low-level bond finder, and CLI
    now all carry the same global/pair policy through the pipeline.
    """

    signature = inspect.signature(build_loaded_crystal)
    assert "bond_scale" in signature.parameters
    assert "bond_thresholds" in signature.parameters

    parser = argparse.ArgumentParser()
    render = _build_render_parser(parser.add_subparsers())
    args = render.parse_args(
        [
            "structure.cif",
            "-o",
            "figure.png",
            "--bond-scale",
            "1.05",
            "--bond-threshold",
            "Zn,N=2.5",
        ]
    )
    assert args.bond_scale == 1.05
    assert bond_thresholds_from_args(args) == {("N", "Zn"): 2.5}

    atoms = [_atom("C1", "C", 0.0), _atom("C2", "C", 1.95)]
    assert find_bonds(atoms, bond_scale=1.0) == []
    assert find_bonds(atoms, bond_scale=1.05) == [(0, 1)]
