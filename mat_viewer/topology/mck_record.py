"""MolCrysKit polyhedron record for one MatterVis centre.

Kept beside the disorder-policy adapter so shell construction can grow
without moving the shape-label helpers out of ``topology.analysis``.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import numpy as np
from molcrys_kit.analysis.packing_shell import DEFAULT_CENTROID_OFFSET_FRAC

from ..structure import molcrys_bridge
from .disorder_policy import (
    DEFAULT_DISORDER_POLICY,
    center_moiety_formulas,
    invoke_find_polyhedra,
    normalize_disorder_policy,
)

logger = logging.getLogger(__name__)


def _mck_polyhedron_record(
    bundle,
    center_fragment: dict[str, Any],
    cutoff: float,
    *,
    ligand_species: tuple[str, ...] | None = None,
    level: str = "molecule",
    center_species: str | None = None,
    enforce_enclosure: bool = True,
    centroid_offset_frac: float = DEFAULT_CENTROID_OFFSET_FRAC,
    center_kind: str = "centroid",
    hard_cutoff: float | None = None,
    fallback_max: int | None = None,
    disorder_policy: str = DEFAULT_DISORDER_POLICY,
) -> dict[str, Any] | None:
    from . import analysis as topology_analysis

    ligand_formula = topology_analysis._ligand_formula_for_molcryskit(ligand_species)
    if level == "atom":
        central_symbol = str(center_species or "").strip()
        if not central_symbol:
            elems = center_fragment.get("elem_set") or []
            central_symbol = str(elems[0]) if elems else ""
        if not central_symbol or not ligand_formula:
            raise ValueError(
                "Atom-level topology requires center_species and ligand_species element symbols."
            )
        crystal = molcrys_bridge.molecular_crystal_from_bundle(bundle)
        public_module = sys.modules.get("mat_viewer.topology")
        find_polyhedra_impl = getattr(public_module, "find_polyhedra", topology_analysis.find_polyhedra)
        # ``hard_cutoff`` is rejected at atom level by MCK: ``cutoff=`` is
        # already the hard radial cap on this level. The normaliser
        # already drops ``hard_cutoff`` whenever level=='atom'; defend
        # here too so a direct caller (script / REST) can't break MCK.
        atom_kwargs: dict[str, Any] = {}
        if fallback_max is not None:
            atom_kwargs["fallback_max"] = int(fallback_max)
        records = invoke_find_polyhedra(
            find_polyhedra_impl,
            crystal,
            central_symbol,
            ligand_formula,
            level="atom",
            disorder_policy=disorder_policy,
            cutoff=float(cutoff),
            enforce_enclosure=bool(enforce_enclosure),
            centroid_offset_frac=float(centroid_offset_frac),
            **atom_kwargs,
            **topology_analysis._mck_override_kwargs(find_polyhedra_impl),
        )
        if not records:
            return None
        center = np.array(center_fragment.get("center", [0.0, 0.0, 0.0]), dtype=float)
        records.sort(
            key=lambda rec: float(
                np.linalg.norm(
                    np.array(rec.get("center_position", [0.0, 0.0, 0.0]), dtype=float)
                    - center
                )
            )
        )
        return records[0]
    source_molecule_index = center_fragment.get("source_molecule_index")
    if source_molecule_index is None:
        raise ValueError(
            f"Fragment {center_fragment.get('label') or center_fragment.get('index')} "
            "does not carry a MolCrysKit source_molecule_index."
        )
    display_formula = center_fragment.get("formula") or center_fragment.get("species")
    if not display_formula or not ligand_formula:
        raise ValueError(
            "Both center and ligand formulas are required for MolCrysKit polyhedra."
        )
    crystal = molcrys_bridge.molecular_crystal_from_bundle(bundle)
    # MatterVis classifies displayed molecular fragments by their heavy-atom
    # formula (for example ``C6N2``), whereas MolCrysKit's molecule-level
    # packing-shell API addresses the complete formula (``C6H14N2``).  The
    # source molecule index is the authoritative bridge between them.  This
    # also covers NH4+ centers, whose display selector is simply ``N``.
    molecules = getattr(crystal, "molecules", None)
    molecule_index = int(source_molecule_index)
    if molecules is not None and 0 <= molecule_index < len(molecules):
        molecule_index = topology_analysis._validated_mck_molecule_index(
            bundle, crystal, molecule_index
        )
        molecule = molecules[molecule_index]
        as_loaded = topology_analysis._as_loaded_molecule_formula(molecule)
        # The displayed fragment can be the fused as-refined formula.
        # Naming that formula under major or exclusive matches no split
        # view, so the query uses each realization of this molecule index.
        # Stub molecules, and releases without the realization API, keep
        # the as-loaded signature and say so.
        policy = normalize_disorder_policy(disorder_policy)
        realized = center_moiety_formulas(molecule, policy)
        if realized:
            center_formulas = [
                formula
                for formula in realized
                if policy == "as_refined" or formula != as_loaded
            ] or list(realized)
        else:
            center_formulas = [as_loaded]
            if policy != "as_refined":
                logger.warning(
                    "Molecule %s has no realization formulas under %s; "
                    "querying the as-loaded formula %s.",
                    molecule_index,
                    policy,
                    as_loaded,
                )
    else:
        # Compatibility for lightweight bridge objects and third-party loaders.
        policy = normalize_disorder_policy(disorder_policy)
        center_formulas = [str(display_formula)]
    # On level="molecule", MCK's ``cutoff`` IS the candidate search radius
    # that feeds gap+enclosure (per MCK PR #32). MV's state-level ``cutoff``
    # is the search radius too, so the kwarg name lines up after the MCK
    # split. ``hard_cutoff`` is opt-in per-spec for the historical
    # "fill the ball" mode (a CN=12 cuboctahedron, for example); leaving it
    # ``None`` keeps the natural first-shell answer.
    public_module = sys.modules.get("mat_viewer.topology")
    find_polyhedra_impl = getattr(public_module, "find_polyhedra", topology_analysis.find_polyhedra)
    extra_kwargs: dict[str, Any] = {}
    if hard_cutoff is not None:
        extra_kwargs["hard_cutoff"] = float(hard_cutoff)
    if fallback_max is not None:
        extra_kwargs["fallback_max"] = int(fallback_max)
    ligand_moiety = molcrys_bridge.formula_to_moiety(ligand_formula)
    records: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for center_formula in center_formulas:
        for item in invoke_find_polyhedra(
            find_polyhedra_impl,
            crystal,
            molcrys_bridge.formula_to_moiety(str(center_formula)),
            ligand_moiety,
            level="molecule",
            disorder_policy=disorder_policy,
            center_kind=str(center_kind or "centroid"),
            cutoff=float(cutoff),
            central_indices=[int(molecule_index)],
            enforce_enclosure=bool(enforce_enclosure),
            centroid_offset_frac=float(centroid_offset_frac),
            **extra_kwargs,
            **topology_analysis._mck_override_kwargs(find_polyhedra_impl),
        ):
            identity = (
                item.get("center_molecule_index"),
                tuple(item.get("disorder_component_ids") or ()),
                tuple(
                    round(float(value), 6)
                    for value in (item.get("center_position") or ())
                ),
            )
            if identity in seen:
                continue
            seen.add(identity)
            records.append(item)
    if not records:
        logger.warning(
            "No polyhedron shell for molecule %s under disorder_policy=%s "
            "and centre formulas %s. The as-refined moiety is not used as "
            "a silent fallback.",
            molecule_index,
            policy,
            center_formulas,
        )
        return None
    chosen = dict(records[0])
    chosen["extra_shells"] = [
        {
            "center_position": item.get("center_position"),
            "shell_coords": item.get("shell_coords") or [],
            "shell_distances": item.get("shell_distances") or [],
            "source_hull": item.get("source_hull") or item.get("hull"),
        }
        for item in records[1:]
    ]
    return chosen

