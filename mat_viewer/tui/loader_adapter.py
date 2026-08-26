"""Adapt canonical MatterVis structure frames to the terminal CrystalIR."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..capabilities import requirements_for_tui, resolve_requirements
from .crystal_ir import AtomIR, BondIR, CrystalIR, Lattice, filter_crystal


def load_for_tui(
    path: str,
    *,
    display_mode: str = "auto",
    input_format: str | None = None,
    type_map: list[str] | None = None,
    frame: int = 0,
) -> CrystalIR:
    """Load any supported atomistic input through the canonical structure IO."""
    resolve_requirements(requirements_for_tui(path, input_format)).require()

    from ..loader import load_structure_input

    structure = load_structure_input(
        path,
        input_format=input_format,
        type_map=type_map,
        frame_indices=[frame],
    )
    selected = structure.frames[0]
    return _load_bundle(
        selected.bundle,
        path=str(structure.path),
        input_format=structure.input_format,
        display_mode=display_mode,
        frame_index=selected.index,
    )


def _load_bundle(
    bundle,
    *,
    path: str,
    input_format: str,
    display_mode: str,
    frame_index: int,
) -> CrystalIR:
    """Adapt one canonical LoadedCrystal frame to the compact terminal IR."""
    from ..loader import build_bundle_scene

    resolved_display_mode = "unit_cell" if display_mode == "auto" else display_mode
    source_metadata = dict(getattr(bundle, "scene", {}) or {})
    include_boundary_replicas = input_format == "cif" and any(
        source_metadata.get("pbc", [True, True, True])
    )
    scene = build_bundle_scene(
        bundle,
        display_mode=resolved_display_mode,
        show_hydrogen=True,
        preset={},
        include_boundary_replicas=include_boundary_replicas,
    )
    canonical_composition = _element_counts_from_raw(bundle.raw_atoms)
    is_cif = input_format == "cif"
    ir = _crystal_ir_from_scene(
        scene,
        source_path=path,
        spacegroup=_extract_spacegroup_from_cif(path) if is_cif else "",
        species_map={
            key: list(value)
            for key, value in bundle.molcrys_analysis.species_map.items()
        },
        per_formula_unit=dict(bundle.molcrys_analysis.per_fu),
        n_molecules=len(bundle.molcrys_analysis.mol_indices),
        source_molecules={
            index: tuple(int(member) for member in members)
            for index, members in enumerate(bundle.molcrys_analysis.mol_indices)
        },
        source_site_atom_count=(
            _cif_source_site_count(path) if is_cif else len(bundle.raw_atoms)
        ),
        expanded_atom_count=len(bundle.raw_atoms),
        canonical_composition=canonical_composition,
        source_atoms=bundle.raw_atoms,
    )
    ir.metadata.update(
        {
            "input_format": input_format,
            "frame_index": frame_index,
            "pbc": source_metadata.get("pbc"),
            "synthetic_cell": source_metadata.get("synthetic_cell", False),
            "bond_source": ("canonical_scene" if is_cif else "distance_heuristic"),
        }
    )
    analysis = bundle.molcrys_analysis
    site_ids = {
        int(record.global_index): str(record.site_id)
        for record in analysis.site_records
        if getattr(record, "site_id", None)
    }
    for atom in ir.atoms:
        atom.atom_id = site_ids.get(atom.source_index, "")
    ir.chemistry = getattr(analysis, "chemistry", None)
    if not is_cif:
        source_indices = {
            index
            for index, atom in enumerate(ir.atoms)
            if atom.image_shift == (0, 0, 0)
        }
        ir = filter_crystal(ir, source_indices, collapse_source_images=True)

    cube = getattr(bundle, "cube_data", None)
    if cube is not None:
        ir.metadata["cube_data"] = cube
        blobs = _extract_density_blobs(cube)
        if blobs:
            ir.metadata["density_blobs"] = blobs
    return ir


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
            str(
                fragment.get("formula")
                or fragment.get("species")
                or display_molecule_index
            ),
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
                dg = 0
        source_index = int(atom.get("_source_index", index))
        image_shift = _source_to_display_shift(atom, source_index, source_atoms)
        atoms.append(
            AtomIR(
                element=str(atom["elem"]),
                cart=np.asarray(atom["cart"], dtype=float),
                frac=np.asarray(atom["frac"], dtype=float),
                label=str(atom.get("label", "")),
                occupancy=float(atom.get("occ", 1.0)),
                index=index,
                source_index=source_index,
                source_instance_id=str(
                    atom.get("_raw_instance_id")
                    or f"{atom.get('label', '')}@sym{atom.get('_symop_index', 0)}"
                ),
                symmetry_operation_index=int(atom.get("_symop_index", 0)),
                image_shift=image_shift,
                display_copy_id=_display_copy_id(atom, source_index, image_shift),
                source_molecule_index=atom_to_source_molecule.get(index, -1),
                display_fragment_id=atom_to_fragment.get(index, ""),
                molecule_index=atom_to_molecule.get(index, -1),
                disorder_group=dg,
                is_minor=bool(atom.get("is_minor", False)),
            )
        )

    bonds: list[BondIR] = []
    for bond in scene.get("bonds", []):
        start = np.asarray(bond["start"], dtype=float)
        end = np.asarray(bond["end"], dtype=float)
        atom_i = atoms[int(bond["i"])]
        atom_j = atoms[int(bond["j"])]
        bonds.append(
            BondIR(
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
            )
        )

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
            "explicit_bond_table": any(
                bool(atom.get("_has_bond_table")) for atom in source_atoms
            ),
            "source_site_atom_count": source_site_atom_count,
            "expanded_atom_count": expanded_atom_count,
            "display_atom_count": len(atoms),
            "rings": tuple(scene.get("rings", ())),
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
    from ..structure.cif_parse import load_cif

    asym_indices = {
        record.asym_index
        for record in load_cif(path).crystal.get_site_records()
        if record.asym_index is not None
    }
    return len(asym_indices)


def _element_counts_from_raw(atoms: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for atom in atoms:
        element = str(atom["elem"])
        counts[element] = counts.get(element, 0) + 1
    return counts


def _extract_spacegroup_from_cif(path: str) -> str:
    """Read declared space-group metadata without a second structure parser."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    tags = (
        "_space_group_name_H-M_alt",
        "_symmetry_space_group_name_H-M",
        "_space_group_name_H-M",
    )
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lower = line.lower()
        for tag in tags:
            if not lower.startswith(tag.lower()):
                continue
            value = line[len(tag) :].strip().strip("'").strip('"')
            if value and value not in {".", "?"}:
                return value
    return ""


def _extract_density_blobs(cube) -> list[dict]:
    """Extract approximate bounding spheres for + and - isosurface lobes.

    Uses connected-component labeling on the thresholded volume to identify
    distinct lobes, then computes their centroid and bounding radius in
    Cartesian space.
    """
    from ..cube.core import default_isovalue

    try:
        from scipy.ndimage import label as ndi_label
    except ImportError as exc:
        install = resolve_requirements(("cube", "tui")).install_command
        raise RuntimeError(
            "Cube density extraction is unavailable. "
            f"Install the exact TUI + Cube requirements with: {install}."
        ) from exc

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
            blobs.append(
                {
                    "center": center,
                    "radius": radius,
                    "sign": sign,
                    "n_voxels": len(indices),
                }
            )

    return blobs


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
