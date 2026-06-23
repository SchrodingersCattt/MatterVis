from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import gemmi
import numpy as np
from ase import Atoms
from ase.geometry import cell_to_cellpar, cellpar_to_cell
from ase.io import read as ase_read
from molcrys_kit.utils.geometry import cart_to_frac, frac_to_cart


@dataclass(frozen=True)
class ExtxyzLoadResult:
    raw_atoms: list[dict[str, Any]]
    cell: gemmi.UnitCell
    M: np.ndarray
    crystal: Any
    source_frame_index: int | None = None
    source_frame_count: int | None = None


def _require_extxyz_support():
    try:
        from molcrys_kit.io.extxyz import read_extxyz
    except Exception:  # pragma: no cover - depends on installed MCK version
        read_extxyz = None
    try:
        from molcrys_kit.structures.crystal import MolecularCrystal
        from molcrys_kit.structures.molecule import CrystalMolecule
    except ImportError as exc:  # pragma: no cover - MCK is a hard dependency in tests
        raise ImportError(
            "molcrys-kit with MolecularCrystal support is required to read extxyz files."
        ) from exc
    return read_extxyz, MolecularCrystal, CrystalMolecule


def is_extxyz_path(path: str | None) -> bool:
    return str(path or "").lower().endswith((".extxyz", ".xyz"))


def _frame_count(path: str) -> int:
    try:
        frames = ase_read(path, index=":", format="extxyz")
    except Exception as exc:
        raise ValueError(f"Failed to parse extxyz file {path!r}: {exc}") from exc
    if frames is None:
        return 0
    if isinstance(frames, Atoms):
        return 1
    return len(frames)


def _canonical_cell(M_original: np.ndarray) -> tuple[gemmi.UnitCell, np.ndarray]:
    cellpar = cell_to_cellpar(M_original)
    a, b, c, alpha, beta, gamma = (float(x) for x in cellpar)
    if not np.all(np.isfinite(cellpar)) or min(a, b, c) <= 1e-8:
        raise ValueError("extxyz file has an invalid or missing lattice.")
    cell = gemmi.UnitCell(a, b, c, alpha, beta, gamma)
    M_canonical = np.asarray(cellpar_to_cell(cellpar), dtype=float)
    volume = abs(float(np.linalg.det(M_canonical)))
    if not np.isfinite(volume) or volume <= 1e-8:
        raise ValueError("extxyz file has a zero-volume lattice.")
    return cell, M_canonical


def _array_values(atoms: Atoms, *names: str):
    for name in names:
        if name in atoms.arrays:
            return atoms.arrays[name]
    return None


def _normal_disorder_value(value: Any) -> str:
    text = str(value if value is not None else ".").strip()
    return text if text not in ("", "None", "nan") else "."


def _copy_optional_arrays(source: Atoms, target: Atoms) -> None:
    for name, values in source.arrays.items():
        if name in {"numbers", "positions"}:
            continue
        try:
            target.set_array(name, np.asarray(values).copy())
        except Exception:
            continue
    target.info.update(dict(getattr(source, "info", {}) or {}))


def _canonical_atoms_from_ase(atoms: Atoms, M_original: np.ndarray, M_canonical: np.ndarray) -> Atoms:
    positions = np.asarray(atoms.get_positions(), dtype=float)
    frac = cart_to_frac(positions, M_original)
    wrapped_frac = frac - np.floor(frac)
    canonical_positions = frac_to_cart(wrapped_frac, M_canonical)
    out = Atoms(
        symbols=atoms.get_chemical_symbols(),
        positions=canonical_positions,
        cell=M_canonical,
        pbc=tuple(bool(x) for x in atoms.get_pbc()),
    )
    _copy_optional_arrays(atoms, out)
    return out


def _molecules_from_molecule_index(atoms: Atoms, CrystalMolecule) -> list[Any]:
    mol_idx = atoms.arrays.get("molecule_index")
    if mol_idx is None:
        return []
    mol_idx = np.asarray(mol_idx, dtype=int)
    molecules = []
    for value in sorted(int(x) for x in np.unique(mol_idx)):
        indices = np.where(mol_idx == value)[0].astype(int).tolist()
        if not indices:
            continue
        mol_atoms = atoms[indices]
        mol_atoms.info["atom_indices"] = indices
        molecule = CrystalMolecule(mol_atoms, check_pbc=False)
        molecule.info["atom_indices"] = indices
        molecules.append(molecule)
    return molecules


def _canonical_crystal_from_atoms(original_crystal: Any, atoms: Atoms, MolecularCrystal, CrystalMolecule):
    molecules = _molecules_from_molecule_index(atoms, CrystalMolecule)
    if not molecules:
        molecules = []
        offset = 0
        for molecule in getattr(original_crystal, "molecules", []) or []:
            n_atoms = len(molecule)
            indices = list(range(offset, offset + n_atoms))
            mol_atoms = atoms[indices]
            mol_atoms.info["atom_indices"] = indices
            new_molecule = CrystalMolecule(mol_atoms, check_pbc=False)
            new_molecule.info["atom_indices"] = indices
            molecules.append(new_molecule)
            offset += n_atoms
    return MolecularCrystal(
        np.asarray(atoms.get_cell(), dtype=float),
        molecules,
        pbc=tuple(bool(x) for x in atoms.get_pbc()),
        formula_moiety=getattr(original_crystal, "formula_moiety", None),
        disorder_provenance=getattr(original_crystal, "disorder_provenance", None),
    )


def _raw_atoms_from_ase(atoms: Atoms, M: np.ndarray) -> list[dict[str, Any]]:
    labels = _array_values(atoms, "label", "labels", "_atom_site_label")
    occs = _array_values(atoms, "occupancy", "occ")
    uisos = _array_values(atoms, "uiso", "U_iso", "u_iso")
    dgs = _array_values(atoms, "disorder_group", "dg")
    das = _array_values(atoms, "assembly", "disorder_assembly", "da")
    mol_idx = _array_values(atoms, "molecule_index")

    positions = np.asarray(atoms.get_positions(), dtype=float)
    frac = cart_to_frac(positions, np.asarray(M, dtype=float))
    raw_atoms: list[dict[str, Any]] = []
    for idx, (symbol, cart, frac_coord) in enumerate(zip(atoms.get_chemical_symbols(), positions, frac)):
        elem = str(symbol).strip().capitalize()
        label = str(labels[idx]).strip() if labels is not None else f"{elem}{idx + 1}"
        if not label or label in {".", "?", "None"}:
            label = f"{elem}{idx + 1}"
        try:
            occ = float(occs[idx]) if occs is not None else 1.0
        except (TypeError, ValueError):
            occ = 1.0
        try:
            uiso = float(uisos[idx]) if uisos is not None else 0.04
        except (TypeError, ValueError):
            uiso = 0.04
        atom = {
            "label": label,
            "elem": elem,
            "cart": np.asarray(cart, dtype=float).copy(),
            "frac": np.asarray(frac_coord, dtype=float).copy(),
            "occ": occ,
            "uiso": uiso,
            "dg": _normal_disorder_value(dgs[idx]) if dgs is not None else ".",
            "da": _normal_disorder_value(das[idx]) if das is not None else ".",
            "_source_index": int(idx),
            "_bond_partners": (),
            "_bond_lengths": {},
            "_has_bond_table": False,
        }
        if mol_idx is not None:
            try:
                atom["_extxyz_molecule_index"] = int(mol_idx[idx])
            except (TypeError, ValueError):
                pass
        raw_atoms.append(atom)
    return raw_atoms


def parse_extxyz(path: str, *, frame_index: int | None = 0) -> ExtxyzLoadResult:
    read_extxyz, MolecularCrystal, CrystalMolecule = _require_extxyz_support()
    n_frames = _frame_count(path)
    if n_frames <= 0:
        raise ValueError(f"No frames found in extxyz file {path!r}.")
    selected = 0 if frame_index is None else int(frame_index)
    if selected < 0 or selected >= n_frames:
        raise ValueError(f"frame_index {selected} is out of range for {n_frames} extxyz frame(s).")

    original_atoms = None
    if read_extxyz is not None:
        crystal = read_extxyz(path, index=selected)
    else:
        original_atoms = ase_read(path, index=selected, format="extxyz")
        crystal = MolecularCrystal.from_ase(original_atoms)
    if isinstance(crystal, list):
        raise ValueError("MatterVis expects a single extxyz frame; pass a frame_index.")

    flat_atoms = crystal.to_ase() if hasattr(crystal, "to_ase") else None
    if flat_atoms is None or not isinstance(flat_atoms, Atoms):
        raise ValueError("MolCrysKit could not flatten extxyz MolecularCrystal to ASE Atoms.")
    if original_atoms is not None and len(original_atoms) == len(flat_atoms):
        _copy_optional_arrays(original_atoms, flat_atoms)

    M_original = np.asarray(getattr(crystal, "lattice", flat_atoms.get_cell()), dtype=float)
    if M_original.shape != (3, 3) or not np.all(np.isfinite(M_original)):
        raise ValueError("extxyz file has an invalid 3x3 lattice.")
    cell, M_canonical = _canonical_cell(M_original)
    canonical_atoms = _canonical_atoms_from_ase(flat_atoms, M_original, M_canonical)
    canonical_crystal = _canonical_crystal_from_atoms(crystal, canonical_atoms, MolecularCrystal, CrystalMolecule)
    raw_atoms = _raw_atoms_from_ase(canonical_atoms, M_canonical)
    return ExtxyzLoadResult(
        raw_atoms=raw_atoms,
        cell=cell,
        M=M_canonical,
        crystal=canonical_crystal,
        source_frame_index=selected,
        source_frame_count=n_frames,
    )


__all__ = ["ExtxyzLoadResult", "is_extxyz_path", "parse_extxyz"]
