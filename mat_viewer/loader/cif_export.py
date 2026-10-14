"""Serialize canonical MatterVis structures back to CIF.

The loader keeps MolCrysKit's :class:`MolecularCrystal` alongside the render
scene.  CIF export deliberately uses that object rather than reconstructing a
crystal from display atoms: display modes and transforms are presentation
state, while the loaded crystal is the source structure the user asked to
download.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

import numpy as np


def crystal_to_cif(crystal: Any) -> str:
    """Return a CIF string using MolCrysKit's public writer.

    ``write_cif`` returns text when no filename is supplied.  Keeping this
    tiny adapter here gives the app and API one serialization contract and
    makes the optional dependency boundary easy to test.
    """

    if crystal is None:
        raise ValueError("the loaded structure has no molecular crystal to export")
    try:
        from molcrys_kit.io import write_cif
    except ImportError as exc:  # pragma: no cover - base install includes MCK
        raise RuntimeError(
            "CIF export requires MolCrysKit; install MatterVis's base dependencies"
        ) from exc
    text = write_cif(crystal)
    if not isinstance(text, str):
        raise TypeError("MolCrysKit CIF writer returned non-text output")
    return text if text.endswith("\n") else text + "\n"


def _cif_token(value: Any, *, default: str = ".") -> str:
    """Render one scalar CIF token without allowing a newline injection."""

    text = str(value if value not in (None, "") else default).strip()
    text = re.sub(r"[\r\n]+", " ", text)
    if any(char.isspace() for char in text) or text[:1] in {"#", "'", '"'}:
        return "'" + text.replace("'", "''") + "'"
    return text


def _cell_parameters(bundle: Any) -> tuple[float, float, float, float, float, float]:
    cell = getattr(bundle, "cell", None)
    names = ("a", "b", "c", "alpha", "beta", "gamma")
    values = (
        tuple(float(getattr(cell, name)) for name in names)
        if cell is not None and all(hasattr(cell, name) for name in names)
        else None
    )
    if values is not None:
        return values

    matrix = np.asarray(getattr(bundle, "M", np.eye(3)), dtype=float)
    lengths = np.linalg.norm(matrix, axis=1)
    if np.any(lengths <= 0):
        raise ValueError("cannot export CIF: structure cell is degenerate")

    def angle(first: np.ndarray, second: np.ndarray) -> float:
        denominator = np.linalg.norm(first) * np.linalg.norm(second)
        cosine = float(np.dot(first, second) / denominator)
        return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))

    # CIF alpha is angle(b,c), beta=(a,c), gamma=(a,b).
    return (
        float(lengths[0]),
        float(lengths[1]),
        float(lengths[2]),
        angle(matrix[1], matrix[2]),
        angle(matrix[0], matrix[2]),
        angle(matrix[0], matrix[1]),
    )


def _raw_atoms_to_cif(bundle: Any) -> str:
    """Small dependency-free fallback for synthetic/test bundles.

    Normal bundles always carry ``bundle.crystal``.  This fallback keeps CIF
    export useful for a caller that provides a canonical ``LoadedCrystal``
    assembled from raw atoms but has not run the chemistry adapter yet.
    """

    atoms: Iterable[dict[str, Any]] = getattr(bundle, "raw_atoms", ()) or ()
    matrix = np.asarray(getattr(bundle, "M", np.eye(3)), dtype=float)
    inverse = (
        np.linalg.inv(matrix)
        if abs(float(np.linalg.det(matrix))) > 1e-12
        else None
    )
    rows: list[tuple[str, str, np.ndarray, Any, Any, Any]] = []
    for index, atom in enumerate(atoms, start=1):
        symbol = str(atom.get("elem") or atom.get("symbol") or "X")
        label = str(atom.get("label") or f"{symbol}{index}")
        frac = atom.get("frac")
        if frac is None:
            cart = np.asarray(atom.get("cart", (0.0, 0.0, 0.0)), dtype=float)
            frac = cart @ inverse if inverse is not None else cart
        rows.append(
            (
                label,
                symbol,
                np.asarray(frac, dtype=float),
                atom.get("occ", 1.0),
                atom.get("dg", "."),
                atom.get("da", "."),
            )
        )

    a, b, c, alpha, beta, gamma = _cell_parameters(bundle)
    lines = [
        "data_mattervis",
        "_audit_creation_method 'MatterVis'",
        "_symmetry_space_group_name_H-M 'P 1'",
        "_symmetry_Int_Tables_number 1",
        "",
        "loop_",
        "  _symmetry_equiv_pos_as_xyz",
        "  x,y,z",
        "",
        f"_cell_length_a {a:.6f}",
        f"_cell_length_b {b:.6f}",
        f"_cell_length_c {c:.6f}",
        f"_cell_angle_alpha {alpha:.6f}",
        f"_cell_angle_beta {beta:.6f}",
        f"_cell_angle_gamma {gamma:.6f}",
        "",
        "loop_",
        "  _atom_site_label",
        "  _atom_site_type_symbol",
        "  _atom_site_fract_x",
        "  _atom_site_fract_y",
        "  _atom_site_fract_z",
        "  _atom_site_occupancy",
        "  _atom_site_disorder_group",
        "  _atom_site_disorder_assembly",
    ]
    for label, symbol, frac, occupancy, disorder_group, assembly in rows:
        lines.append(
            f"  {_cif_token(label)} {_cif_token(symbol)} "
            f"{float(frac[0]):.8f} {float(frac[1]):.8f} {float(frac[2]):.8f} "
            f"{float(occupancy if occupancy is not None else 1.0):.6f} "
            f"{_cif_token(disorder_group)} {_cif_token(assembly)}"
        )
    return "\n".join(lines) + "\n"


def loaded_crystal_to_cif(bundle: Any) -> str:
    """Serialize a ``LoadedCrystal`` while preserving source-structure scope."""

    crystal = getattr(bundle, "crystal", None)
    if crystal is not None:
        return crystal_to_cif(crystal)
    return _raw_atoms_to_cif(bundle)


__all__ = ["crystal_to_cif", "loaded_crystal_to_cif"]
