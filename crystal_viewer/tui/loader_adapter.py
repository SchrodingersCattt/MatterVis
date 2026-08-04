"""Load crystal structures into CrystalIR from various file formats.

Supported formats:
- CIF (.cif) — via existing MatterVis parser (gemmi-based)
- POSCAR/VASP (.vasp, .poscar, POSCAR, CONTCAR) — via pymatgen
- Extended XYZ (.extxyz, .xyz) — via ASE
- Gaussian/CP2K cube (.cube) — structure + volumetric density metadata
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .crystal_ir import AtomIR, BondIR, CrystalIR, Lattice


def load_for_tui(path: str, *, display_mode: str = "auto") -> CrystalIR:
    """Load a crystal structure file and return a CrystalIR.

    Dispatches to the appropriate parser based on file extension.
    CIF files reuse MatterVis's canonical loader and scene assembly so the
    terminal view observes the same disorder, formula-unit, and PBC bond
    semantics as the browser and static renderers.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Structure file not found: {path}")

    ext = p.suffix.lower()
    name = p.stem

    if ext == ".cif":
        ir = _load_cif(str(p), name, display_mode=display_mode)
    elif ext == ".cube":
        ir = _load_cube(str(p), name)
    elif ext in (".vasp", ".poscar") or p.name.upper() in ("POSCAR", "CONTCAR"):
        ir = _load_poscar(str(p), name)
    elif ext in (".extxyz", ".xyz"):
        ir = _load_extxyz(str(p), name)
    else:
        raise ValueError(
            f"Unsupported file format: {ext!r}. "
            f"Supported: .cif, .cube, .vasp, .poscar, .extxyz, .xyz"
        )

    return ir


# ── CIF loader (reuses existing MatterVis parser + MCK) ─────────────────────


def _load_cif(path: str, name: str, *, display_mode: str) -> CrystalIR:
    """Load a CIF through the canonical MatterVis loader."""
    from ..loader import build_bundle_scene, build_loaded_crystal

    bundle = build_loaded_crystal(
        name=name,
        cif_path=path,
        title=name,
        source="upload",
    )
    resolved_display_mode = display_mode
    if display_mode == "auto":
        resolved_display_mode = "unit_cell"
    scene = build_bundle_scene(
        bundle,
        display_mode=resolved_display_mode,
        show_hydrogen=True,
        preset={},
    )
    canonical_composition = _element_counts_from_raw(bundle.raw_atoms)
    return _crystal_ir_from_scene(
        scene,
        source_path=path,
        spacegroup=_extract_spacegroup_from_cif(path),
        species_map={key: list(value) for key, value in bundle.molcrys_analysis.species_map.items()},
        per_formula_unit=dict(bundle.molcrys_analysis.per_fu),
        n_molecules=len(bundle.molcrys_analysis.mol_indices),
        source_molecules={
            index: tuple(int(member) for member in members)
            for index, members in enumerate(bundle.molcrys_analysis.mol_indices)
        },
        source_site_atom_count=_cif_source_site_count(path),
        expanded_atom_count=len(bundle.raw_atoms),
        canonical_composition=canonical_composition,
        source_atoms=bundle.raw_atoms,
    )


def _crystal_ir_from_scene(
    scene: dict,
    *,
    source_path: str,
    spacegroup: str,
    species_map: dict[str, list[int]],
    per_formula_unit: dict[str, int],
    n_molecules: int,
    source_molecules: dict[int, tuple[int, ...]],
    source_site_atom_count: int,
    expanded_atom_count: int,
    canonical_composition: dict[str, int],
    source_atoms: list[dict],
) -> CrystalIR:
    """Adapt a canonical scene into the terminal renderer's compact IR."""
    cell = scene["cell"]
    lattice = Lattice(
        a=cell.a,
        b=cell.b,
        c=cell.c,
        alpha=cell.alpha,
        beta=cell.beta,
        gamma=cell.gamma,
        matrix=np.asarray(scene["M"], dtype=float),
    )

    source_species: dict[int, str] = {
        int(molecule_index): species_id
        for species_id, molecule_indices in species_map.items()
        for molecule_index in molecule_indices
    }
    atom_to_molecule: dict[int, int] = {}
    atom_to_fragment: dict[int, str] = {}
    atom_to_source_molecule: dict[int, int] = {}
    molecule_species: dict[int, str] = {}
    for display_molecule_index, fragment in enumerate(scene.get("fragment_table", [])):
        source_molecule_index = fragment.get("source_molecule_index")
        if source_molecule_index is None:
            continue
        source_molecule_index = int(source_molecule_index)
        molecule_species[display_molecule_index] = source_species.get(
            source_molecule_index,
            str(fragment.get("formula") or fragment.get("species") or display_molecule_index),
        )
        for atom_index in fragment.get("site_indices", []):
            atom_to_molecule[int(atom_index)] = display_molecule_index
            atom_to_fragment[int(atom_index)] = str(
                fragment.get("label") or f"fragment:{display_molecule_index}"
            )
            atom_to_source_molecule[int(atom_index)] = source_molecule_index

    atoms: list[AtomIR] = []
    for index, atom in enumerate(scene.get("draw_atoms", [])):
        dg = 0
        dg_raw = str(atom.get("dg", "") or "").strip()
        if dg_raw not in ("", ".", "?"):
            try:
                dg = int(float(dg_raw))
            except ValueError:
                pass
        source_index = int(atom.get("_source_index", index))
        image_shift = _source_to_display_shift(atom, source_index, source_atoms)
        atoms.append(AtomIR(
            element=str(atom["elem"]),
            cart=np.asarray(atom["cart"], dtype=float),
            frac=np.asarray(atom["frac"], dtype=float),
            label=str(atom.get("label", "")),
            occupancy=float(atom.get("occ", 1.0)),
            index=index,
            source_index=source_index,
            source_instance_id=str(
                atom.get("_raw_instance_id") or f"{atom.get('label', '')}@sym{atom.get('_symop_index', 0)}"
            ),
            symmetry_operation_index=int(atom.get("_symop_index", 0)),
            image_shift=image_shift,
            display_copy_id=_display_copy_id(atom, source_index, image_shift),
            source_molecule_index=atom_to_source_molecule.get(index, -1),
            display_fragment_id=atom_to_fragment.get(index, ""),
            molecule_index=atom_to_molecule.get(index, -1),
            disorder_group=dg,
            is_minor=bool(atom.get("is_minor", False)),
        ))

    bonds: list[BondIR] = []
    for bond in scene.get("bonds", []):
        start = np.asarray(bond["start"], dtype=float)
        end = np.asarray(bond["end"], dtype=float)
        atom_i = atoms[int(bond["i"])]
        atom_j = atoms[int(bond["j"])]
        bonds.append(BondIR(
            i=int(bond["i"]),
            j=int(bond["j"]),
            distance=float(np.linalg.norm(end - start)),
            start=start,
            end=end,
            start_display_copy_id=atom_i.display_copy_id,
            end_display_copy_id=atom_j.display_copy_id,
            image_relation=tuple(
                atom_j.image_shift[axis] - atom_i.image_shift[axis]
                for axis in range(3)
            ),
        ))

    display_species_map: dict[str, list[int]] = {}
    for display_molecule_index, species_id in molecule_species.items():
        display_species_map.setdefault(species_id, []).append(display_molecule_index)

    formula = _compose_formula(atoms)
    return CrystalIR(
        title=str(scene.get("title") or scene.get("name") or Path(source_path).stem),
        formula=formula,
        spacegroup=spacegroup,
        source_path=source_path,
        canonical_formula=_formula_from_counts(canonical_composition),
        canonical_composition=canonical_composition,
        source_site_atom_count=source_site_atom_count,
        expanded_atom_count=expanded_atom_count,
        lattice=lattice,
        atoms=atoms,
        bonds=bonds,
        n_molecules=n_molecules,
        species_map=display_species_map or species_map,
        source_molecules=source_molecules,
        source_molecule_species=source_species,
        per_formula_unit=per_formula_unit,
        metadata={
            "display_mode": scene.get("display_mode", "unit_cell"),
            "bond_source": "canonical_scene",
            "explicit_bond_table": any(bool(atom.get("_has_bond_table")) for atom in source_atoms),
            "source_site_atom_count": source_site_atom_count,
            "expanded_atom_count": expanded_atom_count,
            "display_atom_count": len(atoms),
        },
    )


def _display_copy_id(
    atom: dict,
    source_index: int,
    image_shift: tuple[int, int, int],
) -> str:
    instance = str(
        atom.get("_raw_instance_id")
        or f"{atom.get('label', '')}@sym{atom.get('_symop_index', 0)}"
    )
    return (
        f"{instance}/source:{source_index}/"
        f"image:{image_shift[0]},{image_shift[1]},{image_shift[2]}"
    )


def _source_to_display_shift(
    atom: dict,
    source_index: int,
    source_atoms: list[dict],
) -> tuple[int, int, int]:
    if 0 <= source_index < len(source_atoms):
        source_frac = np.asarray(source_atoms[source_index]["frac"], dtype=float)
    else:
        wrapped = atom.get("_wrapped_frac")
        if wrapped is None:
            explicit = atom.get("_image_shift") or (0, 0, 0)
            return tuple(int(value) for value in explicit)
        source_frac = np.asarray(wrapped, dtype=float)
    display_frac = np.asarray(atom["frac"], dtype=float)
    return tuple(int(value) for value in np.rint(display_frac - source_frac))


def _cif_source_site_count(path: str) -> int:
    import gemmi

    block = gemmi.cif.read(path).sole_block()
    return len(list(block.find_loop("_atom_site_label")))


def _element_counts_from_raw(atoms: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in atoms:
        element = str(atom["elem"])
        counts[element] = counts.get(element, 0) + 1
    return counts


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


# ── Cube file loader ────────────────────────────────────────────────────────


def _load_cube(path: str, name: str) -> CrystalIR:
    """Load Gaussian/CP2K .cube file.

    Structure goes through MCK bond detection (same as CIF).
    Volumetric data is stored in metadata for TUI density blob rendering.
    """
    from ..cube import read_cube
    from ..cube.bridge import cube_lattice_matrix, cube_to_raw_atoms
    from ..structure.bonds import find_bonds

    cube = read_cube(path)
    M = cube_lattice_matrix(cube)
    raw_atoms = cube_to_raw_atoms(cube)

    # Build lattice from matrix
    a_vec, b_vec, c_vec = M[0], M[1], M[2]
    a = float(np.linalg.norm(a_vec))
    b = float(np.linalg.norm(b_vec))
    c = float(np.linalg.norm(c_vec))
    alpha = float(np.degrees(np.arccos(np.clip(np.dot(b_vec, c_vec) / (b * c), -1, 1))))
    beta = float(np.degrees(np.arccos(np.clip(np.dot(a_vec, c_vec) / (a * c), -1, 1))))
    gamma = float(np.degrees(np.arccos(np.clip(np.dot(a_vec, b_vec) / (a * b), -1, 1))))
    lattice = Lattice(a=a, b=b, c=c, alpha=alpha, beta=beta, gamma=gamma, matrix=M)

    # MCK bond detection
    import gemmi
    cell = gemmi.UnitCell(a, b, c, alpha, beta, gamma)
    bond_pairs: list[tuple[int, int]] = []
    try:
        from ..structure.molcrys_bridge import analyze as mck_analyze
        mck_analysis = mck_analyze(raw_atoms, M)
        bond_pairs = mck_analysis.bond_pairs
    except Exception:
        try:
            bond_pairs = find_bonds(raw_atoms, M=M, cell=cell)
        except Exception:
            pass

    # Convert to AtomIR
    atoms = []
    for i, at in enumerate(raw_atoms):
        label = at.get("label", f"{at['elem']}{i+1}")
        atoms.append(AtomIR(
            element=at["elem"],
            cart=np.array(at["cart"], dtype=float),
            frac=np.array(at["frac"], dtype=float),
            label=label,
            occupancy=1.0,
            index=i,
            source_index=i,
            source_instance_id=f"{label}@source:{i}",
            display_copy_id=f"source:{i}/image:0,0,0",
        ))

    bonds = []
    for pair in bond_pairs:
        i, j = pair[0], pair[1]
        if i < len(atoms) and j < len(atoms):
            d = float(np.linalg.norm(atoms[i].cart - atoms[j].cart))
            bonds.append(BondIR(i=i, j=j, distance=d))

    formula = _compose_formula(atoms)
    counts = _counts_from_atoms(atoms)

    ir = CrystalIR(
        title=name,
        formula=formula,
        spacegroup="",
        source_path=path,
        canonical_formula=formula,
        canonical_composition=counts,
        source_site_atom_count=len(atoms),
        expanded_atom_count=len(atoms),
        lattice=lattice,
        atoms=atoms,
        bonds=bonds,
        metadata={
            "display_mode": "structure",
            "bond_source": "distance_heuristic",
            "explicit_bond_table": False,
            "source_site_atom_count": len(atoms),
            "expanded_atom_count": len(atoms),
            "display_atom_count": len(atoms),
        },
    )

    # Store cube data for density blob rendering
    ir.metadata["cube_data"] = cube

    # Pre-compute density blobs (bounding spheres of isosurface lobes)
    try:
        blobs = _extract_density_blobs(cube)
        if blobs:
            ir.metadata["density_blobs"] = blobs
    except Exception:
        pass

    return ir


def _extract_density_blobs(cube) -> list[dict]:
    """Extract approximate bounding spheres for + and - isosurface lobes.

    Uses connected-component labeling on the thresholded volume to identify
    distinct lobes, then computes their centroid and bounding radius in
    Cartesian space.
    """
    from ..cube.core import default_isovalue

    try:
        from scipy.ndimage import label as ndi_label
    except ImportError:
        return []

    iso = default_isovalue(cube.values)
    blobs = []

    for sign, threshold in [(+1, iso), (-1, -iso)]:
        if sign > 0:
            mask = cube.values > threshold
        else:
            mask = cube.values < threshold
        if not mask.any():
            continue

        labeled, n_features = ndi_label(mask)
        for comp_id in range(1, n_features + 1):
            # Find voxel indices of this component
            indices = np.argwhere(labeled == comp_id)
            if len(indices) < 4:
                continue
            # Convert voxel indices to Cartesian coordinates
            # Each voxel (i, j, k) → origin + i*axes[0] + j*axes[1] + k*axes[2]
            cart_points = (
                cube.origin[None, :]
                + indices[:, 0:1] * cube.axes[0][None, :]
                + indices[:, 1:2] * cube.axes[1][None, :]
                + indices[:, 2:3] * cube.axes[2][None, :]
            )
            center = cart_points.mean(axis=0)
            radius = float(np.max(np.linalg.norm(cart_points - center, axis=1)))
            blobs.append({
                "center": center,
                "radius": radius,
                "sign": sign,
                "n_voxels": len(indices),
            })

    return blobs


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
            source_index=i,
            source_instance_id=f"{elem}{i+1}@source:{i}",
            display_copy_id=f"source:{i}/image:0,0,0",
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
    counts = _counts_from_atoms(atoms)
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
        canonical_formula=formula,
        canonical_composition=counts,
        source_site_atom_count=len(atoms),
        expanded_atom_count=len(atoms),
        lattice=lattice,
        atoms=atoms,
        bonds=bonds,
        metadata={
            "display_mode": "structure",
            "bond_source": "distance_heuristic",
            "explicit_bond_table": False,
            "source_site_atom_count": len(atoms),
            "expanded_atom_count": len(atoms),
            "display_atom_count": len(atoms),
        },
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
    atoms_raw = []  # dict format for find_bonds compatibility
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
            source_index=i,
            source_instance_id=f"{sym}{i+1}@source:{i}",
            display_copy_id=f"source:{i}/image:0,0,0",
        ))
        atoms_raw.append({
            "elem": sym, "cart": pos, "frac": frac,
            "label": f"{sym}{i+1}", "occ": 1.0,
            "dg": ".", "da": ".",
            "_bond_partners": (), "_bond_lengths": {},
            "_has_bond_table": False,
        })

    formula = _compose_formula(atoms)
    counts = _counts_from_atoms(atoms)

    # Distance-heuristic bond detection (same approach as POSCAR/VASP)
    bonds: list[BondIR] = []
    bond_source = "none"
    if len(atoms_raw) > 0:
        try:
            from ..structure.bonds import find_bonds

            import gemmi

            if has_cell and lattice is not None:
                M = np.array(lattice.matrix)
                cell = gemmi.UnitCell(
                    lattice.a, lattice.b, lattice.c,
                    lattice.alpha, lattice.beta, lattice.gamma,
                )
                bond_pairs = find_bonds(atoms_raw, M=M, cell=cell)
            else:
                bond_pairs = find_bonds(atoms_raw)

            for i, j in bond_pairs:
                d = float(np.linalg.norm(atoms[i].cart - atoms[j].cart))
                bonds.append(BondIR(i=i, j=j, distance=d))
            if bonds:
                bond_source = "distance_heuristic"
        except Exception:
            pass  # Bonds are optional

    return CrystalIR(
        title=name,
        formula=formula,
        spacegroup="",
        source_path=path,
        canonical_formula=formula,
        canonical_composition=counts,
        source_site_atom_count=len(atoms),
        expanded_atom_count=len(atoms),
        lattice=lattice,
        atoms=atoms,
        bonds=bonds,
        metadata={
            "display_mode": "structure",
            "bond_source": bond_source,
            "explicit_bond_table": False,
            "source_site_atom_count": len(atoms),
            "expanded_atom_count": len(atoms),
            "display_atom_count": len(atoms),
        },
    )


def _compose_formula(atoms: list[AtomIR]) -> str:
    """Build an absolute composition formula from an atom list."""
    counts = _counts_from_atoms(atoms)

    return _formula_from_counts(counts)


def _counts_from_atoms(atoms: list[AtomIR]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in atoms:
        counts[atom.element] = counts.get(atom.element, 0) + 1
    return counts


def _formula_from_counts(counts: dict[str, int]) -> str:
    """Build an absolute composition formula from element counts."""

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
