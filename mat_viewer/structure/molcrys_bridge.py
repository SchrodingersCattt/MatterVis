"""Adapter between MatterVis raw atom dicts and MolCrysKit's molecule
stoichiometry pipeline.

MatterVis carries each atom as a Python dict (``elem``, ``cart``,
``frac``, ``label``, ``occ``, ``da``, ``dg``, ...) so the rest of the
renderer can read every per-atom field directly.  MolCrysKit operates
on ASE :class:`~ase.Atoms` objects with extra disorder metadata
arrays.  This module translates between the two so the formula-unit
picker and the fragment legend can lean on MolCrysKit's
:class:`StoichiometryAnalyzer` (graph-isomorphism based species ID +
GCD-derived per-FU counts) instead of MatterVis's old hand-rolled
heuristics.
"""

from __future__ import annotations

import copy
import re

import numpy as np
import molcrys_kit
from molcrys_kit.utils.geometry import cart_to_frac, frac_to_cart

from ..style.disorder import atom_is_minor
from .chemistry_records import (
    AbsoluteStructureRecord,
    AtomChemistryRecord,
    BondChemistryRecord,
    CrystalChemistryRecords,
    EntityChemistryRecord,
)


class StructureContractError(RuntimeError):
    """Raised when MolCrysKit's renderer-facing structure contract is absent."""


def _require_molcryskit():
    """Import MolCrysKit lazily and surface a clean error if missing."""
    try:
        from ase import Atoms

        from molcrys_kit.structures.crystal import MolecularCrystal
        from molcrys_kit.io.cif import identify_molecules
        from molcrys_kit.analysis.interactions import LocalGeometryCache
        from molcrys_kit.analysis.stoichiometry import StoichiometryAnalyzer
        from molcrys_kit.constants.config import (
            KEY_OCCUPANCY,
            KEY_DISORDER_GROUP,
            KEY_ASSEMBLY,
            KEY_LABEL,
            KEY_SYM_OP_INDEX,
            KEY_ASYM_ID,
            KEY_SITE_SYMMETRY_ORDER,
            KEY_IMAGE_SHIFT,
            KEY_UISO,
            KEY_U_CART,
        )
    except ImportError as exc:
        from ..capabilities import (
            MOLCRYSKIT_INSTALL,
            MOLCRYSKIT_MINIMUM,
        )

        raise ImportError(
            "MolCrysKit is a base MatterVis dependency and is required for "
            f"formula_unit display (molcrys-kit>={MOLCRYSKIT_MINIMUM}). "
            f"Install or upgrade it with: {MOLCRYSKIT_INSTALL}."
        ) from exc

    from ..capabilities import (
        MOLCRYSKIT_INSTALL,
        MOLCRYSKIT_MINIMUM,
        molcryskit_contract_missing,
    )

    missing = molcryskit_contract_missing()
    if missing:
        raise RuntimeError(
            "The installed molcrys-kit does not provide MatterVis's required "
            "public structure contracts: "
            + ", ".join(missing)
            + f". MatterVis requires molcrys-kit>={MOLCRYSKIT_MINIMUM}. "
            "Install or upgrade it with: "
            + MOLCRYSKIT_INSTALL
            + "."
        )

    return {
        "Atoms": Atoms,
        "MolecularCrystal": MolecularCrystal,
        "identify_molecules": identify_molecules,
        "StoichiometryAnalyzer": StoichiometryAnalyzer,
        "LocalGeometryCache": LocalGeometryCache,
        "KEY_OCCUPANCY": KEY_OCCUPANCY,
        "KEY_DISORDER_GROUP": KEY_DISORDER_GROUP,
        "KEY_ASSEMBLY": KEY_ASSEMBLY,
        "KEY_LABEL": KEY_LABEL,
        "KEY_SYM_OP_INDEX": KEY_SYM_OP_INDEX,
        "KEY_ASYM_ID": KEY_ASYM_ID,
        "KEY_SITE_SYMMETRY_ORDER": KEY_SITE_SYMMETRY_ORDER,
        "KEY_IMAGE_SHIFT": KEY_IMAGE_SHIFT,
        "KEY_UISO": KEY_UISO,
        "KEY_U_CART": KEY_U_CART,
        "infer_chemistry": getattr(molcrys_kit, "infer_chemistry", None),
        "assign_stereochemistry": getattr(
            molcrys_kit,
            "assign_stereochemistry",
            None,
        ),
    }


def _ase_atoms_from_raw(raw_atoms, M, mk):
    """Build an index-aligned ASE Atoms with MolCrysKit disorder arrays.

    MatterVis stores the lattice as a 3x3 matrix whose **rows** are
    the a, b, c vectors, matching ASE and MolCrysKit.
    """
    symbols = [atom["elem"] for atom in raw_atoms]
    positions = np.array([atom["cart"] for atom in raw_atoms], dtype=float)
    cell = np.asarray(M, dtype=float)

    atoms = mk["Atoms"](
        symbols=symbols,
        positions=positions,
        cell=cell,
        pbc=True,
    )
    n = len(raw_atoms)

    occ = np.empty(n, dtype=float)
    for i, atom in enumerate(raw_atoms):
        try:
            occ[i] = float(atom.get("occ", 1.0))
        except (TypeError, ValueError):
            occ[i] = 1.0

    dg = np.zeros(n, dtype=int)
    for i, atom in enumerate(raw_atoms):
        value = str(atom.get("dg", "") or "").strip()
        if value in ("", ".", "?"):
            continue
        try:
            dg[i] = int(float(value))
        except (TypeError, ValueError):
            dg[i] = 0
    for i, atom in enumerate(raw_atoms):
        if dg[i] != 0:
            continue
        # Occupancy-only rotamer disorder (e.g. DAP-4 NH4+) has no CIF PART
        # label, so MCK would see every alternative as group 0 and allow
        # cross-orientation close contacts to fuse. Reuse the optimal-replica
        # picker result as a synthetic PART label only at the MCK adapter
        # boundary; raw atom dictionaries keep their original dg/da fields.
        if atom.get("_is_minor") is True:
            dg[i] = -1
        elif atom.get("_is_major") is True or atom.get("_is_minor") is False:
            dg[i] = 1

    da = np.array([(str(atom.get("da", "") or "").strip()) for atom in raw_atoms])
    da = np.array([("" if v in (".", "?") else v) for v in da])

    label = np.array([atom.get("label") or atom["elem"] for atom in raw_atoms])
    sym_op_index = np.array(
        [int(atom.get("_symop_index", 0) or 0) for atom in raw_atoms], dtype=int
    )
    asym_index = np.array(
        [int(atom.get("_asym_index", i)) for i, atom in enumerate(raw_atoms)], dtype=int
    )
    site_symmetry_order = np.array(
        [int(atom.get("_site_symmetry_order", 1) or 1) for atom in raw_atoms], dtype=int
    )
    image_shift = np.asarray(
        [
            atom.get("_mck_image_shift", atom.get("_image_shift", (0, 0, 0)))
            for atom in raw_atoms
        ],
        dtype=int,
    ).reshape(n, 3)
    uiso = np.asarray(
        [
            np.nan if atom.get("uiso") is None else float(atom["uiso"])
            for atom in raw_atoms
        ],
        dtype=float,
    )
    u_cart = np.full((n, 9), np.nan, dtype=float)
    for i, atom in enumerate(raw_atoms):
        value = atom.get("U")
        if value is None:
            continue
        matrix = np.asarray(value, dtype=float)
        if matrix.shape == (3, 3) and np.all(np.isfinite(matrix)):
            u_cart[i] = matrix.reshape(9)

    atoms.set_array(mk["KEY_OCCUPANCY"], occ)
    atoms.set_array(mk["KEY_DISORDER_GROUP"], dg)
    atoms.set_array(mk["KEY_ASSEMBLY"], da)
    atoms.set_array(mk["KEY_LABEL"], label)
    atoms.set_array(mk["KEY_SYM_OP_INDEX"], sym_op_index)
    atoms.set_array(mk["KEY_ASYM_ID"], asym_index)
    atoms.set_array(mk["KEY_SITE_SYMMETRY_ORDER"], site_symmetry_order)
    atoms.set_array(mk["KEY_IMAGE_SHIFT"], image_shift)
    atoms.set_array(mk["KEY_UISO"], uiso)
    atoms.set_array(mk["KEY_U_CART"], u_cart)
    return atoms


def _is_minor_atom(atom) -> bool:
    """Return whether the loader marked this atom as a minor disorder image.

    Raw CIF disorder tags are intentionally ignored here. Ordered
    special-position atoms and unresolved PART records are not safe evidence
    for fading or molecule exclusion without loader-side provenance.
    """
    return atom_is_minor(atom)


def _minor_index_set(raw_atoms) -> set[int]:
    """Indices into ``raw_atoms`` that should be excluded from
    bond perception. After ``_tag_shelx_occupancy_disorder`` has run
    on a disordered CIF, ``_is_minor`` reflects the actual chosen
    optimal orientation -- atoms that didn't make the cut are tagged
    minor and won't bond into any molecule, restoring the correct
    one-orientation-per-disorder-site molecule grouping.
    """
    return {i for i, atom in enumerate(raw_atoms) if _is_minor_atom(atom)}


_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def formula_to_moiety(formula: str) -> str:
    """Convert MatterVis compact formula keys to MolCrysKit moiety strings.

    MatterVis species selectors are compact formula keys such as ``C6N2`` or
    ``ClO4``. MolCrysKit's molecule-level packing-shell API accepts single
    moiety strings such as ``C6 N2`` and ``Cl O4``. This adapter is purely
    syntactic; invalid or multi-moiety values fail instead of falling back to
    MatterVis-local chemistry.
    """
    text = str(formula or "").strip()
    if not text or text == "?":
        raise ValueError(
            f"Cannot convert empty fragment formula to moiety: {formula!r}"
        )
    parts: list[str] = []
    pos = 0
    for match in _FORMULA_TOKEN_RE.finditer(text):
        if match.start() != pos:
            raise ValueError(
                f"Invalid compact formula for MolCrysKit moiety: {formula!r}"
            )
        elem, count = match.groups()
        parts.append(f"{elem}{count}" if count else elem)
        pos = match.end()
    if pos != len(text) or not parts:
        raise ValueError(f"Invalid compact formula for MolCrysKit moiety: {formula!r}")
    return " ".join(parts)


def molecular_crystal_from_bundle(bundle):
    """Return the MolCrysKit ``MolecularCrystal`` already built for a bundle."""
    analysis = getattr(bundle, "molcrys_analysis", None)
    crystal = getattr(analysis, "crystal", None) or getattr(bundle, "crystal", None)
    if crystal is None:
        raise ValueError("Bundle has no MolCrysKit MolecularCrystal analysis.")
    if (
        getattr(crystal, "molecules", None) is None
        or getattr(crystal, "lattice", None) is None
    ):
        raise TypeError(
            "MolCrysKit molecule-level polyhedra require .molecules and .lattice."
        )
    return crystal


class CrystalAnalysis:
    """MolCrysKit-derived chemistry on the unit cell.

    Attributes
    ----------
    crystal:
        :class:`MolecularCrystal` instance MolCrysKit returns.
    mol_indices:
        ``mol_indices[k]`` lists the original raw_atom indices spanned
        by the k-th molecule in ``crystal.molecules``.
    mol_cart_positions:
        ``mol_cart_positions[k]`` stores MolCrysKit's PBC-unwrapped
        Cartesian coordinates for the same molecule.  These are what
        formula-unit rendering must draw; using the wrapped raw atom
        coordinates reintroduces long MIC-crossing bonds.
    species_map:
        ``species_id -> [mol_idx, ...]``.  Species IDs come from
        :class:`StoichiometryAnalyzer` and look like ``C6H14N2_1``.
    per_fu:
        ``species_id -> count`` after dividing every species' cell
        count by the GCD across all species.  Canonical Z=1 stoich.
    """

    def __init__(
        self,
        crystal,
        mol_indices,
        mol_cart_positions,
        species_map,
        per_fu,
        bond_pairs=None,
        bond_records=None,
        site_records=None,
        formula_unit_selection=None,
        ring_records=None,
        chemistry=None,
    ):
        self.crystal = crystal
        self.mol_indices = mol_indices
        self.mol_cart_positions = mol_cart_positions
        self.species_map = species_map
        self.per_fu = per_fu
        # ``bond_pairs`` is the canonical molecule-graph edge list in raw_atom
        # indices, sorted (i < j). It is the single source of truth for bond
        # connectivity in the unit cell. Downstream code that needs a bond
        # list (renderer, fragment-table builder) MUST consume this rather
        # than calling the legacy ``ops.find_bonds`` again, otherwise it
        # reintroduces the disorder/PBC mishandling that produced the
        # "?-orphan-H" / variable-cluster_size NH4 bugs (DAP-4, SY).
        self.bond_pairs: list[tuple[int, int]] = list(bond_pairs or [])
        # ``bond_records`` extend ``bond_pairs`` with MolCrysKit's signed
        # PBC image relation.  For ``left, right, S`` the physical edge is
        # ``x_right + S @ M - x_left``.  Display assembly uses this relation
        # to materialise the edge on the matching boundary image rather than
        # re-perceiving chemistry on replica atoms.
        self._bond_records_present = bond_records is not None
        self._site_records_present = site_records is not None
        self.bond_records: list[dict] = list(bond_records or [])
        self.site_records = tuple(site_records or ())
        self.formula_unit_selection = formula_unit_selection
        # Rings come from MolCrysKit's molecule-local topology. Both the
        # stable sorted identity and the edge-connected traversal are mapped
        # to raw/global source indices before any display copies are created.
        self.ring_records: list[dict] = list(ring_records or [])
        self.chemistry: CrystalChemistryRecords | None = chemistry


def require_structure_contract(
    analysis,
    *,
    atom_count: int | None = None,
    require_formula_unit: bool = False,
):
    """Validate the complete MolCrysKit contract used by public render paths.

    Empty ``bond_records`` is a valid statement that a structure has no bonds;
    an absent/``None`` attribute is not. The same distinction matters for
    ``site_records`` because their global indices are the stable identity used
    to lift PBC bonds onto displayed images.
    """
    if analysis is None:
        raise StructureContractError(
            "MatterVis requires a MolCrysKit CrystalAnalysis built from "
            "get_site_records() and get_bond_records(); no analysis was supplied."
        )

    site_records = getattr(analysis, "site_records", None)
    bond_records = getattr(analysis, "bond_records", None)
    if site_records is None or getattr(analysis, "_site_records_present", True) is False:
        raise StructureContractError(
            "MolCrysKit analysis is missing public SiteRecord data."
        )
    if bond_records is None or getattr(analysis, "_bond_records_present", True) is False:
        raise StructureContractError(
            "MolCrysKit analysis is missing public BondRecord data."
        )

    sites = tuple(site_records)
    if atom_count is not None:
        globals_seen: list[int] = []
        for record in sites:
            try:
                globals_seen.append(int(record.global_index))
            except (AttributeError, TypeError, ValueError) as exc:
                raise StructureContractError(
                    "MolCrysKit SiteRecord is missing a valid global_index."
                ) from exc
        if sorted(globals_seen) != list(range(int(atom_count))):
            raise StructureContractError(
                "MolCrysKit SiteRecord global indices must match the source atom "
                "sequence exactly."
            )

    for record in bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            shift = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StructureContractError(
                "MolCrysKit BondRecord projection is missing left/right/PBC shift."
            ) from exc
        if len(shift) != 3:
            raise StructureContractError(
                "MolCrysKit BondRecord right_image_shift must have three integers."
            )
        if atom_count is not None and not (
            0 <= left < int(atom_count) and 0 <= right < int(atom_count)
        ):
            raise StructureContractError(
                "MolCrysKit BondRecord endpoint is outside the SiteRecord sequence."
            )

    if require_formula_unit:
        selection = getattr(analysis, "formula_unit_selection", None)
        if selection is None or not hasattr(selection, "members"):
            raise StructureContractError(
                "MolCrysKit analysis is missing FormulaUnitSelection.members."
            )
        if atom_count and not tuple(selection.members):
            raise StructureContractError(
                "MolCrysKit returned an empty FormulaUnitSelection for a non-empty "
                "structure."
            )
    return analysis


def atoms_with_site_provenance(raw_atoms, analysis):
    """Copy atom dicts and attach stable identities from public SiteRecords."""
    atoms = [copy.deepcopy(atom) for atom in raw_atoms]
    require_structure_contract(analysis, atom_count=len(atoms))
    records = sorted(analysis.site_records, key=lambda record: int(record.global_index))
    for atom, record in zip(atoms, records):
        source_index = int(record.global_index)
        frac = np.asarray(atom.get("frac", record.fractional_position), dtype=float)
        atom["_source_index"] = source_index
        atom["_source_molecule_index"] = int(record.molecule_index)
        atom.setdefault("_molecule_index", int(record.molecule_index))
        atom.setdefault("_molecule_local_index", int(record.local_index))
        atom.setdefault("_wrapped_frac", frac - np.floor(frac))
        atom.setdefault("_mck_image_shift", list(record.image_shift))
        if getattr(record, "site_id", None):
            atom["_mck_atom_id"] = str(record.site_id)
    return atoms


def _chemistry_records(crystal, mk) -> CrystalChemistryRecords | None:
    """Copy MolCrysKit chemistry/stereo reports without re-deriving them."""
    infer_chemistry = mk.get("infer_chemistry")
    assign_stereochemistry = mk.get("assign_stereochemistry")
    if not callable(infer_chemistry) or not callable(assign_stereochemistry):
        return None

    chemistry = getattr(crystal, "chemistry", None) or infer_chemistry(crystal)
    source_by_atom_id = {
        str(atom_id): source_index
        for source_index, atom_id in enumerate(chemistry.atom_ids_by_global_index)
    }
    atom_records: list[AtomChemistryRecord] = []
    bond_records: list[BondChemistryRecord] = []
    entity_records: list[EntityChemistryRecord] = []
    warnings = list(str(value) for value in chemistry.warnings)
    cif_chemistry = dict(getattr(crystal, "metadata", {}).get("cif_chemistry", {}))

    for entity in chemistry.components:
        try:
            stereo_report = assign_stereochemistry(entity, entity.embedding)
        except (TypeError, ValueError) as exc:
            stereo_report = None
            warnings.append(f"{entity.entity_id}: stereochemistry unavailable: {exc}")
        stereo_by_atom = {
            descriptor.center_atom_id: descriptor
            for descriptor in getattr(stereo_report, "descriptors", ())
        }
        entity_evidence = _evidence_text(getattr(entity, "evidence", ()))
        dimension = getattr(entity, "dimension", None)
        net_charge = getattr(
            entity,
            "net_charge",
            getattr(entity, "net_charge_per_repeat", None),
        )
        entity_records.append(
            EntityChemistryRecord(
                entity_id=str(entity.entity_id),
                kind=type(entity).__name__,
                dimension=None if dimension is None else int(dimension),
                atom_ids=tuple(str(atom.atom_id) for atom in entity.atoms),
                net_charge=None if net_charge is None else int(net_charge),
                status=_enum_text(entity.status),
                translation_generators=tuple(
                    tuple(int(value) for value in vector)
                    for vector in getattr(entity, "translation_generators", ())
                ),
                warnings=tuple(str(value) for value in entity.warnings),
                evidence=entity_evidence,
            )
        )
        for atom in entity.atoms:
            stereo = stereo_by_atom.get(atom.atom_id)
            atom_records.append(
                AtomChemistryRecord(
                    atom_id=str(atom.atom_id),
                    source_index=int(source_by_atom_id[str(atom.atom_id)]),
                    entity_id=str(entity.entity_id),
                    element=str(atom.element),
                    isotope=None if atom.isotope is None else int(atom.isotope),
                    formal_charge=(
                        None if atom.formal_charge is None else int(atom.formal_charge)
                    ),
                    radical_electrons=int(atom.radical_electrons),
                    implicit_hydrogens=(
                        None
                        if atom.implicit_hydrogens is None
                        else int(atom.implicit_hydrogens)
                    ),
                    oxidation_state=(
                        None if atom.oxidation_state is None else int(atom.oxidation_state)
                    ),
                    status=_enum_text(entity.status),
                    stereo_descriptor=(
                        None if stereo is None else stereo.descriptor
                    ),
                    stereo_kind=(
                        None if stereo is None else _enum_text(stereo.kind)
                    ),
                    stereo_status=(
                        None if stereo is None else _enum_text(stereo.status)
                    ),
                    cip_order=(
                        () if stereo is None else tuple(str(value) for value in stereo.cip_order)
                    ),
                    stereo_reason=(None if stereo is None else str(stereo.reason)),
                    evidence=_evidence_text(atom.evidence),
                )
            )
        bond_records.extend(
            BondChemistryRecord(
                atom1_id=str(bond.atom1_id),
                atom2_id=str(bond.atom2_id),
                order=None if bond.order is None else float(bond.order),
                kind=_enum_text(bond.kind),
                aromatic=bool(bond.aromatic),
                atom2_image_shift=tuple(int(value) for value in bond.atom2_image_shift),
                stereochemistry=(
                    None if bond.stereochemistry is None else str(bond.stereochemistry)
                ),
                evidence=_evidence_text(bond.evidence),
            )
            for bond in entity.bonds
        )
        warnings.extend(str(value) for value in getattr(stereo_report, "warnings", ()))

    absolute_source = dict(cif_chemistry.get("absolute_structure", {}))
    absolute_records = tuple(
        AbsoluteStructureRecord(
            method=method,
            raw=str(record["raw"]),
            value=float(record["value"]),
            standard_uncertainty=(
                None
                if record.get("standard_uncertainty") is None
                else float(record["standard_uncertainty"])
            ),
        )
        for method in ("flack", "hooft", "rogers")
        if isinstance((record := absolute_source.get(method)), dict)
        and record.get("value") is not None
    )
    source_names = tuple(
        (kind, str(cif_chemistry[key]))
        for kind, key in (
            ("systematic", "chemical_name_systematic"),
            ("common", "chemical_name_common"),
        )
        if cif_chemistry.get(key)
    )
    return CrystalChemistryRecords(
        status=_enum_text(chemistry.status),
        atoms=tuple(sorted(atom_records, key=lambda record: record.source_index)),
        bonds=tuple(bond_records),
        entities=tuple(entity_records),
        warnings=tuple(dict.fromkeys(warnings)),
        evidence=_evidence_text(chemistry.evidence),
        source_names=source_names,
        absolute_configuration=(
            None
            if not cif_chemistry.get("chemical_absolute_configuration")
            else str(cif_chemistry["chemical_absolute_configuration"])
        ),
        absolute_structure=absolute_records,
        absolute_structure_details=(
            None
            if not absolute_source.get("details")
            else str(absolute_source["details"])
        ),
    )


def _enum_text(value) -> str:
    return str(getattr(value, "value", value))


def _evidence_text(values) -> tuple[str, ...]:
    records = []
    for value in values:
        source = _enum_text(value.source)
        text = f"{source}:{value.method}"
        if value.detail:
            text += f" ({value.detail})"
        records.append(text)
    return tuple(records)


def analyze_crystal(crystal) -> CrystalAnalysis:
    """Build MatterVis lookup tables from MolCrysKit public contracts only."""
    mk = _require_molcryskit()
    site_records = tuple(crystal.get_site_records())
    contract_bonds = tuple(crystal.get_bond_records())

    sites_by_molecule: dict[int, list] = {}
    for record in site_records:
        sites_by_molecule.setdefault(record.molecule_index, []).append(record)

    mol_indices: list[list[int]] = []
    mol_cart_positions: list[np.ndarray] = []
    for molecule_index in range(len(crystal.molecules)):
        records = sorted(
            sites_by_molecule.get(molecule_index, ()),
            key=lambda record: record.local_index,
        )
        mol_indices.append([int(record.global_index) for record in records])
        mol_cart_positions.append(
            np.asarray([record.cartesian_position_A for record in records], dtype=float)
        )

    bond_pairs = sorted(
        {
            tuple(sorted((record.left_global_index, record.right_global_index)))
            for record in contract_bonds
        }
    )
    site_image_shifts = {
        int(record.global_index): np.asarray(record.image_shift, dtype=int)
        for record in site_records
    }
    bond_records = [
        {
            "left": int(record.left_global_index),
            "right": int(record.right_global_index),
            "left_local_index": int(record.left_local_index),
            "right_local_index": int(record.right_local_index),
            "molecule_index": int(record.molecule_index),
            "left_asym_index": record.left_asym_index,
            "right_asym_index": record.right_asym_index,
            "right_image_shift": list(
                np.asarray(record.right_image_shift, dtype=int)
                + site_image_shifts[int(record.right_global_index)]
                - site_image_shifts[int(record.left_global_index)]
            ),
            "vector": list(record.vector_A),
            "distance": float(record.distance_A),
        }
        for record in contract_bonds
    ]

    ring_records: list[dict] = []
    local_geometries = mk["LocalGeometryCache"](crystal)
    for molecule_index, global_indices in enumerate(mol_indices):
        for ring_index, ring in enumerate(local_geometries[molecule_index].rings()):
            cycle_local = tuple(int(index) for index in ring.cycle_atom_indices)
            sorted_local = tuple(int(index) for index in ring.atom_indices)
            if not cycle_local:
                raise RuntimeError(
                    "MolCrysKit RingGeometry is missing cycle_atom_indices; "
                    "install the structure-contract release or the exact "
                    "development commit pinned by MatterVis CI."
                )
            try:
                cycle_global = tuple(global_indices[index] for index in cycle_local)
                sorted_global = tuple(global_indices[index] for index in sorted_local)
            except IndexError as exc:
                raise RuntimeError(
                    "MolCrysKit returned a ring index outside its parent molecule."
                ) from exc
            ring_records.append(
                {
                    "molecule_index": molecule_index,
                    "ring_index": ring_index,
                    "atom_indices": sorted_global,
                    "cycle_atom_indices": cycle_global,
                    "symbols": tuple(str(symbol) for symbol in ring.symbols),
                    "centroid_A": tuple(float(value) for value in ring.centroid_A),
                    "normal": tuple(float(value) for value in ring.normal),
                    "plane_rmsd_A": (
                        None if ring.plane_rmsd_A is None else float(ring.plane_rmsd_A)
                    ),
                    "is_planar": bool(ring.is_planar),
                    "is_aromatic": bool(ring.is_aromatic),
                    "size": int(ring.size or len(sorted_global)),
                }
            )

    analyzer = mk["StoichiometryAnalyzer"](crystal)
    return CrystalAnalysis(
        crystal=crystal,
        mol_indices=mol_indices,
        mol_cart_positions=mol_cart_positions,
        species_map=copy.deepcopy(analyzer.species_map),
        per_fu=copy.deepcopy(analyzer.get_simplest_unit()),
        bond_pairs=bond_pairs,
        bond_records=bond_records,
        site_records=site_records,
        formula_unit_selection=analyzer.select_formula_unit(),
        ring_records=ring_records,
        chemistry=_chemistry_records(crystal, mk),
    )


def analyze(
    raw_atoms,
    M,
    *,
    max_atoms=None,
    bond_scale: float = 1.0,
    bond_thresholds=None,
):
    """Run MolCrysKit on ``raw_atoms`` (full unit cell) and return a
    :class:`CrystalAnalysis` summarising species + per-FU counts.

    MolCrysKit's ``identify_molecules`` is disorder-aware: atoms in
    incompatible non-zero PART groups are not bonded even when their
    Cartesian positions overlap. MatterVis passes CIF disorder groups
    through directly, and synthesises a private +1/-1 group at the ASE
    adapter boundary for occupancy-only rotamers already classified by
    ``_tag_shelx_occupancy_disorder``. Both major and minor alternatives
    therefore remain full molecular fragments in ``mol_indices``; the
    renderer still distinguishes them via the original ``_is_minor`` flag.
    """
    mk = _require_molcryskit()
    if not raw_atoms:
        crystal = mk["MolecularCrystal"](np.eye(3), [], pbc=(True, True, True))
        return analyze_crystal(crystal)

    ase_atoms = _ase_atoms_from_raw(raw_atoms, M, mk)
    identified = mk["identify_molecules"](
        ase_atoms,
        max_atoms=max_atoms,
        bond_scale=bond_scale,
        bond_thresholds=bond_thresholds,
    )

    crystal = mk["MolecularCrystal"](
        ase_atoms.get_cell(), identified, pbc=tuple(ase_atoms.get_pbc())
    )
    return analyze_crystal(crystal)


def _translate_cluster(raw_atoms, indices, shift_frac, M, cart_positions=None):
    M = np.asarray(M, dtype=float)
    shift_cart = frac_to_cart(shift_frac, M)
    out = []
    for local_idx, i in enumerate(indices):
        atom = copy.deepcopy(raw_atoms[i])
        base_cart = (
            np.asarray(cart_positions[local_idx], dtype=float)
            if cart_positions is not None
            else np.asarray(atom["cart"], dtype=float)
        )
        atom["cart"] = base_cart + shift_cart
        atom["frac"] = cart_to_frac(atom["cart"], M)
        # Preserve the raw_atoms index on every translated copy so the
        # fragment-table builder (which consumes mol_indices into raw_atoms)
        # can still figure out which molecule each formula-unit atom
        # belongs to. Without this, formula_unit-mode draw_atoms lose their
        # provenance and we'd have to re-derive the grouping by Cartesian
        # proximity -- which is exactly the legacy mistake we're eliminating.
        atom["_source_index"] = int(i)
        out.append(atom)
    return out


def select_formula_unit(raw_atoms, M, *, analysis=None):
    """Materialise MolCrysKit's deterministic compact formula-unit selection."""
    if analysis is None:
        analysis = analyze(raw_atoms, M)
    require_structure_contract(
        analysis,
        atom_count=len(raw_atoms),
        require_formula_unit=True,
    )
    selection = analysis.formula_unit_selection
    if not selection.members:
        return []

    M = np.asarray(M, dtype=float)
    chosen_atoms = []
    for member in selection.members:
        molecule_index = int(member.molecule_index)
        translated = _translate_cluster(
            raw_atoms,
            analysis.mol_indices[molecule_index],
            member.image_shift,
            M,
            cart_positions=analysis.mol_cart_positions[molecule_index],
        )
        for atom in translated:
            atom["_molecule_index"] = molecule_index
            atom["_formula_species_id"] = member.species_id
            atom["_formula_image_shift"] = list(member.image_shift)
        chosen_atoms.extend(translated)

    return chosen_atoms


__all__ = [
    "CrystalAnalysis",
    "StructureContractError",
    "analyze",
    "analyze_crystal",
    "atoms_with_site_provenance",
    "formula_to_moiety",
    "molecular_crystal_from_bundle",
    "require_structure_contract",
    "select_formula_unit",
]
