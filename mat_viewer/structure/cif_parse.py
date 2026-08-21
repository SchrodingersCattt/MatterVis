"""CIF adapter backed exclusively by MolCrysKit's public structure contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class CellParameters:
    """Small renderer-facing unit-cell record with no parser dependency."""

    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float
    volume: float


@dataclass(frozen=True)
class ParsedCif:
    """MolCrysKit crystal plus the legacy atom-dict projection."""

    path: Path
    crystal: Any
    atoms: tuple[dict[str, Any], ...]
    cell: CellParameters
    matrix: np.ndarray


def _angle(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom <= 0.0:
        raise ValueError("cell vectors must have positive length")
    cosine = float(np.dot(left, right) / denom)
    return float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))


def cell_from_matrix(matrix: np.ndarray) -> CellParameters:
    lattice = np.asarray(matrix, dtype=float)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        raise ValueError("MolCrysKit returned an invalid 3x3 lattice")
    lengths = np.linalg.norm(lattice, axis=1)
    if np.any(lengths <= 0.0):
        raise ValueError("cell vectors must have positive length")
    return CellParameters(
        a=float(lengths[0]),
        b=float(lengths[1]),
        c=float(lengths[2]),
        alpha=_angle(lattice[1], lattice[2]),
        beta=_angle(lattice[0], lattice[2]),
        gamma=_angle(lattice[0], lattice[1]),
        volume=float(abs(np.linalg.det(lattice))),
    )


def _require_contracts():
    try:
        from molcrys_kit.structures import MolecularCrystal
    except ImportError as exc:
        raise ImportError(
            "CIF input requires MolCrysKit. Install MatterVis's base dependencies."
        ) from exc

    missing = [
        name
        for name in ("get_site_records", "get_bond_records")
        if not callable(getattr(MolecularCrystal, name, None))
    ]
    if missing:
        raise RuntimeError(
            "The installed MolCrysKit is too old for MatterVis: missing public "
            + ", ".join(missing)
            + ". Install the exact development revision pinned by MatterVis CI."
        )
    return MolecularCrystal


def _raw_atoms_from_crystal(crystal) -> tuple[dict[str, Any], ...]:
    records = tuple(crystal.get_site_records())
    by_global = {record.global_index: record for record in records}
    atoms: list[dict[str, Any]] = []
    for record in records:
        frac = np.asarray(record.fractional_position, dtype=float)
        sym_op = record.sym_op_index
        asym_index = record.asym_index
        atom = {
            "label": record.label,
            "elem": record.symbol,
            "frac": frac.copy(),
            "cart": np.asarray(record.cartesian_position_A, dtype=float),
            "occ": float(record.occupancy),
            "uiso": record.uiso_A2,
            "dg": str(record.disorder_group),
            "da": record.disorder_assembly or ".",
            "U": (
                None
                if record.u_cart_A2 is None
                else np.asarray(record.u_cart_A2, dtype=float)
            ),
            "_source_index": int(record.global_index),
            "_molecule_index": int(record.molecule_index),
            "_molecule_local_index": int(record.local_index),
            "_asym_index": asym_index,
            "_asym_label": record.label,
            "_symop_index": sym_op,
            "_site_symmetry_order": int(record.site_symmetry_order),
            # MCK's shift makes this molecule contiguous. ``_image_shift`` is
            # reserved by the scene layer for explicit rendered replicas.
            "_mck_image_shift": list(record.image_shift),
            "_image_shift": [0, 0, 0],
            "_wrapped_frac": frac - np.floor(frac),
            "_raw_instance_id": (
                f"asu{asym_index if asym_index is not None else 'unknown'}"
                f"@sym{sym_op if sym_op is not None else 'unknown'}"
                f"@site{record.global_index}"
            ),
            # Connectivity is supplied separately by get_bond_records().
            "_bond_partners": (),
            "_bond_lengths": {},
            "_has_bond_table": False,
        }
        atoms.append(atom)

    if set(by_global) != set(range(len(records))):
        raise RuntimeError(
            "MolCrysKit site records must use a contiguous global_index sequence"
        )
    atoms.sort(key=lambda atom: atom["_source_index"])
    return tuple(atoms)


def load_cif(path: str | Path) -> ParsedCif:
    """Load a CIF once and retain MolCrysKit as the chemical source of truth."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"CIF file not found: {source}")
    MolecularCrystal = _require_contracts()
    crystal = MolecularCrystal.from_cif(str(source), use_asu_first=False)
    matrix = np.asarray(crystal.lattice, dtype=float)
    return ParsedCif(
        path=source,
        crystal=crystal,
        atoms=_raw_atoms_from_crystal(crystal),
        cell=cell_from_matrix(matrix),
        matrix=matrix,
    )


def parse_asu(path):
    """Return the historical triple, now projected from MolCrysKit records.

    The function name remains for internal callers. The returned sites are the
    symmetry-expanded molecular crystal, not a second independently parsed ASU.
    ``legacy_M`` stores lattice vectors as columns for compatibility with the
    existing scene assembly boundary.
    """
    parsed = load_cif(path)
    return [dict(atom) for atom in parsed.atoms], parsed.cell, parsed.matrix.T.copy()


__all__ = [
    "CellParameters",
    "ParsedCif",
    "cell_from_matrix",
    "load_cif",
    "parse_asu",
]
