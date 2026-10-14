"""CIF adapter backed exclusively by MolCrysKit's public structure contracts."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# CIF ``_atom_site_type_symbol`` values are often written as ionic/species
# labels (``Fe3+``, ``Fe+3``, ``O2-`` or ``Cl-``).  ASE, which MolCrysKit
# uses for its canonical crystal object, accepts element symbols only.  Keep
# charge notation out of the parser input while retaining the original path
# and all other CIF metadata.  Charge/oxidation semantics are owned by
# MolCrysKit's chemistry layer; this adapter only makes the element token
# syntactically valid for its public CIF reader.
_CHARGED_ELEMENT = re.compile(
    r"^(?P<element>[A-Z][a-z]?)(?:(?P<magnitude_before>\d+)(?P<sign_before>[+-])|"
    r"(?P<sign_after>[+-])(?P<magnitude_after>\d*))$"
)


def _normalise_element_symbol(value: str) -> str:
    """Return an ASE-compatible element symbol for a CIF type token."""
    token = str(value).strip()
    match = _CHARGED_ELEMENT.fullmatch(token)
    return match.group("element") if match else token


def _normalise_cif_text(text: str) -> str:
    """Strip ionic charge suffixes from atom-site type-symbol loop values.

    The normaliser intentionally operates only on loops containing
    ``_atom_site_type_symbol``.  A global replacement would corrupt formula
    metadata such as ``_chemical_formula_moiety 'Fe3+ O2-'``.  Most CIF
    writers put one atom row on each line; rows that use multiline values are
    left untouched and continue through MolCrysKit unchanged.
    """
    lines = text.splitlines(keepends=True)
    output = list(lines)
    index = 0
    while index < len(lines):
        if lines[index].strip().lower() != "loop_":
            index += 1
            continue
        header_start = index + 1
        headers: list[str] = []
        cursor = header_start
        while cursor < len(lines):
            stripped = lines[cursor].strip()
            if not stripped or stripped.startswith("#"):
                cursor += 1
                continue
            if not stripped.startswith("_"):
                break
            # Header names do not contain whitespace; a trailing comment is
            # not part of the name.
            headers.append(stripped.split()[0].lower())
            cursor += 1
        type_index = next(
            (pos for pos, header in enumerate(headers)
             if header == "_atom_site_type_symbol"),
            None,
        )
        if type_index is None:
            index = cursor
            continue
        data_index = cursor
        width = len(headers)
        while data_index < len(lines):
            raw = lines[data_index]
            stripped = raw.strip()
            if not stripped:
                data_index += 1
                continue
            if stripped.lower() == "loop_" or stripped.startswith("_"):
                break
            if stripped.startswith(";"):
                # CIF multiline text values cannot be safely rewritten using
                # a line-local token index.
                data_index += 1
                continue
            try:
                tokens = shlex.split(stripped, comments=False, posix=True)
            except ValueError:
                data_index += 1
                continue
            if len(tokens) == width and type_index < len(tokens):
                symbol = _normalise_element_symbol(tokens[type_index])
                if symbol != tokens[type_index]:
                    # Re-emit this row as valid CIF tokens.  Quoting values
                    # containing spaces is required; ordinary atom rows are
                    # unchanged apart from the charged symbol itself.
                    rendered: list[str] = []
                    for token in tokens:
                        if any(ch.isspace() for ch in token) or token == "#":
                            rendered.append("'" + token.replace("'", "''") + "'")
                        else:
                            rendered.append(token)
                    rendered[type_index] = symbol
                    newline = "\n" if raw.endswith("\n") else ""
                    output[data_index] = " ".join(rendered) + newline
            data_index += 1
        index = data_index
    return "".join(output)


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
    # ``MolecularCrystal.from_cif`` accepts paths only.  Use MolCrysKit's
    # public text reader when an atom-site type token carries oxidation
    # notation; this keeps the original upload untouched while avoiding ASE's
    # ``KeyError('Fe3+')``.  The normal path remains the direct fast path.
    text = source.read_text(encoding="utf-8")
    normalised = _normalise_cif_text(text)
    if normalised == text:
        crystal = MolecularCrystal.from_cif(str(source), use_asu_first=False)
    else:
        from molcrys_kit.io.cif import read_mol_crystal

        crystal = read_mol_crystal(cif_text=normalised)
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
