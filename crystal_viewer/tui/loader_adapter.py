"""Load crystal structures into CrystalIR from various file formats.

Supported formats:
- CIF (.cif) — via existing MatterVis parser (gemmi-based)
- POSCAR/VASP (.vasp, .poscar, POSCAR, CONTCAR) — via pymatgen
- Extended XYZ (.extxyz, .xyz) — via ASE
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .crystal_ir import AtomIR, BondIR, CrystalIR, Lattice


def load_for_tui(path: str) -> CrystalIR:
    """Load a crystal structure file and return a CrystalIR.

    Dispatches to the appropriate parser based on file extension.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Structure file not found: {path}")

    ext = p.suffix.lower()
    name = p.stem

    if ext == ".cif":
        return _load_cif(str(p), name)
    elif ext in (".vasp", ".poscar") or p.name.upper() in ("POSCAR", "CONTCAR"):
        return _load_poscar(str(p), name)
    elif ext in (".extxyz", ".xyz"):
        return _load_extxyz(str(p), name)
    else:
        raise ValueError(
            f"Unsupported file format: {ext!r}. "
            f"Supported: .cif, .vasp, .poscar, .extxyz, .xyz"
        )


# ── CIF loader (reuses existing MatterVis parser + MCK) ─────────────────────


def _load_cif(path: str, name: str) -> CrystalIR:
    """Load CIF via parse_asu + MCK molecule analysis.

    Uses MolCrysKit for:
    - Disorder-aware bond detection (bond_pairs)
    - Molecule grouping (molecule_index per atom)
    - Species identification
    """
    import gemmi

    from ..structure.cif_parse import parse_asu
    from ..structure.molcrys_bridge import analyze as mck_analyze
    from ..style.disorder import atom_is_minor

    atoms_raw, cell, M = parse_asu(path)

    # Extract spacegroup name from CIF
    spacegroup = _extract_spacegroup_from_cif(path)

    # Build lattice
    lattice = Lattice(
        a=cell.a, b=cell.b, c=cell.c,
        alpha=cell.alpha, beta=cell.beta, gamma=cell.gamma,
        matrix=M,
    )

    # Run MCK analysis for molecule grouping + bonds
    mck_analysis = None
    mol_index_map: dict[int, int] = {}  # raw_atom_idx → molecule_index
    bond_pairs: list[tuple[int, int]] = []
    species_map: dict[str, list[int]] = {}
    n_molecules = 0

    try:
        mck_analysis = mck_analyze(atoms_raw, M)
        # Build atom → molecule mapping
        for mol_idx, indices in enumerate(mck_analysis.mol_indices):
            for atom_idx in indices:
                mol_index_map[atom_idx] = mol_idx
        n_molecules = len(mck_analysis.mol_indices)
        bond_pairs = mck_analysis.bond_pairs
        species_map = {k: list(v) for k, v in mck_analysis.species_map.items()}
    except Exception:
        # Fallback: use simple find_bonds if MCK fails
        from ..structure.bonds import find_bonds
        bond_pairs = find_bonds(atoms_raw, M=M, cell=cell)

    # Convert atoms with MCK enrichment
    atoms = []
    for i, at in enumerate(atoms_raw):
        dg_raw = str(at.get("dg", "") or "").strip()
        dg = 0
        if dg_raw not in ("", ".", "?"):
            try:
                dg = int(float(dg_raw))
            except (TypeError, ValueError):
                pass

        atoms.append(AtomIR(
            element=at["elem"],
            cart=np.array(at["cart"], dtype=float),
            frac=np.array(at["frac"], dtype=float),
            label=at.get("label", ""),
            occupancy=at.get("occ", 1.0),
            index=i,
            molecule_index=mol_index_map.get(i, -1),
            disorder_group=dg,
            is_minor=atom_is_minor(at),
        ))

    # Build bonds from MCK bond_pairs (disorder-aware)
    bonds = []
    for pair in bond_pairs:
        i, j = pair[0], pair[1]
        if i < len(atoms) and j < len(atoms):
            d = float(np.linalg.norm(atoms[i].cart - atoms[j].cart))
            bonds.append(BondIR(i=i, j=j, distance=d))

    # Compose formula from element counts
    formula = _compose_formula(atoms)

    return CrystalIR(
        title=name,
        formula=formula,
        spacegroup=spacegroup,
        source_path=path,
        lattice=lattice,
        atoms=atoms,
        bonds=bonds,
        n_molecules=n_molecules,
        species_map=species_map,
    )


def _extract_spacegroup_from_cif(path: str) -> str:
    """Try to extract spacegroup symbol from CIF file."""
    try:
        import gemmi
        doc = gemmi.cif.read(path)
        block = doc.sole_block()
        for tag in [
            "_space_group_name_H-M_alt",
            "_symmetry_space_group_name_H-M",
            "_space_group_name_H-M",
        ]:
            val = block.find_value(tag)
            if val:
                cleaned = str(val).strip().strip("'").strip('"')
                if cleaned and cleaned not in (".", "?"):
                    return cleaned
        # Try IT number
        it_val = (
            block.find_value("_space_group_IT_number")
            or block.find_value("_symmetry_Int_Tables_number")
        )
        if it_val:
            num = int(gemmi.cif.as_number(it_val))
            sg = gemmi.find_spacegroup_by_number(num)
            if sg:
                return sg.hm
    except Exception:
        pass
    return ""


# ── POSCAR/VASP loader (pymatgen) ──────────────────────────────────────────


def _load_poscar(path: str, name: str) -> CrystalIR:
    """Load POSCAR/VASP file via pymatgen."""
    from pymatgen.core import Structure

    struct = Structure.from_file(path)
    return _from_pymatgen_structure(struct, name, path)


# ── Extended XYZ loader (ASE) ───────────────────────────────────────────────


def _load_extxyz(path: str, name: str) -> CrystalIR:
    """Load extended XYZ via ASE."""
    from ase.io import read as ase_read

    atoms_ase = ase_read(path)
    return _from_ase_atoms(atoms_ase, name, path)


# ── Conversion helpers ──────────────────────────────────────────────────────


def _from_pymatgen_structure(struct, name: str, path: str) -> CrystalIR:
    """Convert a pymatgen Structure to CrystalIR."""
    from ..structure.bonds import find_bonds

    lat = struct.lattice
    M = np.array(lat.matrix)  # rows = a, b, c

    lattice = Lattice(
        a=lat.a, b=lat.b, c=lat.c,
        alpha=lat.alpha, beta=lat.beta, gamma=lat.gamma,
        matrix=M,
    )

    atoms = []
    atoms_raw = []  # dict format for find_bonds compatibility
    for i, site in enumerate(struct):
        elem = str(site.specie)
        cart = np.array(site.coords)
        frac = np.array(site.frac_coords)
        atoms.append(AtomIR(
            element=elem, cart=cart, frac=frac,
            label=f"{elem}{i+1}", occupancy=1.0, index=i,
        ))
        atoms_raw.append({
            "elem": elem, "cart": cart, "frac": frac,
            "label": f"{elem}{i+1}", "occ": 1.0,
            "dg": ".", "da": ".",
            "_bond_partners": (), "_bond_lengths": {},
            "_has_bond_table": False,
        })

    # Find bonds
    import gemmi
    cell = gemmi.UnitCell(lat.a, lat.b, lat.c, lat.alpha, lat.beta, lat.gamma)
    bonds = []
    try:
        bond_pairs = find_bonds(atoms_raw, M=M, cell=cell)
        for i, j in bond_pairs:
            d = float(np.linalg.norm(atoms[i].cart - atoms[j].cart))
            bonds.append(BondIR(i=i, j=j, distance=d))
    except Exception:
        pass  # Bonds are optional for TUI

    formula = _compose_formula(atoms)
    spacegroup = ""
    try:
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
        sga = SpacegroupAnalyzer(struct, symprec=0.1)
        spacegroup = sga.get_space_group_symbol()
    except Exception:
        pass

    return CrystalIR(
        title=name,
        formula=formula,
        spacegroup=spacegroup,
        source_path=path,
        lattice=lattice,
        atoms=atoms,
        bonds=bonds,
    )


def _from_ase_atoms(atoms_ase, name: str, path: str) -> CrystalIR:
    """Convert an ASE Atoms object to CrystalIR."""
    cell_matrix = np.array(atoms_ase.get_cell())
    has_cell = np.linalg.norm(cell_matrix) > 0.01

    lattice = None
    if has_cell:
        lengths = atoms_ase.cell.lengths()
        angles = atoms_ase.cell.angles()
        lattice = Lattice(
            a=lengths[0], b=lengths[1], c=lengths[2],
            alpha=angles[0], beta=angles[1], gamma=angles[2],
            matrix=cell_matrix,
        )

    positions = atoms_ase.get_positions()
    symbols = atoms_ase.get_chemical_symbols()

    atoms = []
    for i, (sym, pos) in enumerate(zip(symbols, positions)):
        frac = np.zeros(3)
        if has_cell:
            try:
                frac = atoms_ase.get_scaled_positions()[i]
            except Exception:
                pass
        atoms.append(AtomIR(
            element=sym, cart=pos, frac=frac,
            label=f"{sym}{i+1}", occupancy=1.0, index=i,
        ))

    formula = _compose_formula(atoms)

    return CrystalIR(
        title=name,
        formula=formula,
        spacegroup="",
        source_path=path,
        lattice=lattice,
        atoms=atoms,
        bonds=[],  # Skip bonds for extxyz (no topology data)
    )


def _compose_formula(atoms: list[AtomIR]) -> str:
    """Build a reduced formula string from atom list."""
    counts: dict[str, int] = {}
    for a in atoms:
        counts[a.element] = counts.get(a.element, 0) + 1

    if not counts:
        return ""

    # Sort by electronegativity convention (C first, H second, then alpha)
    def _sort_key(elem):
        if elem == "C":
            return (0, elem)
        if elem == "H":
            return (1, elem)
        return (2, elem)

    parts = []
    for elem in sorted(counts, key=_sort_key):
        n = counts[elem]
        if n == 1:
            parts.append(elem)
        else:
            parts.append(f"{elem}{n}")
    return "".join(parts)
