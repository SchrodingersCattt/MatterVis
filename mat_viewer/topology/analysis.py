from __future__ import annotations

import inspect
import sys
from collections.abc import Iterable
from typing import Any

import numpy as np
from molcrys_kit.analysis import compute_topo_signature
from molcrys_kit.analysis.packing_shell import (
    DEFAULT_CENTROID_OFFSET_FRAC,
    compute_angular_signature,
    detect_coordination_number,
    detect_prism_vs_antiprism,
    find_polyhedra,
    planarity_analysis,
)
from molcrys_kit.analysis.packing_shell import (
    hull_encloses_center as _hull_encloses_center,
)
from molcrys_kit.analysis.shape import classify_shell
from molcrys_kit.structures.polyhedra import (
    all_ideal_polyhedra,
    convex_hull_payload,
    ideal_polyhedra_for_cn,
)

from ..config import current_config, element_color
from ..structure import molcrys_bridge
from .disorder_policy import (
    DEFAULT_DISORDER_POLICY,
    invoke_find_polyhedra,
    normalize_disorder_policy,
)
from .mck_record import _mck_polyhedron_record

# Dropdown and persisted rows may still carry the historical "(auto)" token.
# It is not a ligand formula. MolCrysKit rejects it, and MatterVis does not
# invent a shell from the other species in the cell.
_AUTO_LIGAND_TOKENS = {"auto", "(auto)", "__auto__"}
EXPLICIT_LIGAND_MESSAGE = (
    "Choose an explicit ligand species. MolCrysKit polyhedra require an "
    "explicit ligand_species; MatterVis no longer derives ligand shells locally."
)


def explicit_ligand_species(value: Any) -> str | None:
    """Return a ligand formula, or None when the value is missing or ``(auto)``."""
    if isinstance(value, (list, tuple)):
        for item in value:
            resolved = explicit_ligand_species(item)
            if resolved:
                return resolved
        return None
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in _AUTO_LIGAND_TOKENS:
        return None
    return text


def _query_ligand_species(ligand_species: Any) -> tuple[str, ...] | None:
    """Drop blank and ``(auto)`` tokens. A string is one formula, not characters."""
    if ligand_species is None:
        return None
    items = (ligand_species,) if isinstance(ligand_species, str) else ligand_species
    resolved: list[str] = []
    for item in items:
        formula = explicit_ligand_species(item)
        if formula and formula not in resolved:
            resolved.append(formula)
    return tuple(resolved) or None


def _ligand_formula_for_molcryskit(ligand_species: Any) -> str:
    """Ligand formula for MolCrysKit, or the explicit-ligand error when there is none."""
    formula = explicit_ligand_species(ligand_species)
    if not formula:
        raise ValueError(EXPLICIT_LIGAND_MESSAGE)
    return formula


__all__ = [
    "DEFAULT_CENTROID_OFFSET_FRAC",
    "EXPLICIT_LIGAND_MESSAGE",
    "_hull_encloses_center",
    "atom_overlay",
    "display_atom_centers_for_spec",
    "analyze_topology",
    "explicit_ligand_species",
    "classify_fragments",
    "classify_shell",
    "compute_angular_signature",
    "convex_hull_payload",
    "detect_coordination_number",
    "detect_prism_vs_antiprism",
    "extract_atom_coordination_shells",
    "extract_coordination_shell",
    "ideal_polyhedra_for_cn",
    "planarity_analysis",
]




def _mck_override_kwargs(func) -> dict[str, object]:
    """Return explicit MolCrysKit override kwargs supported by ``func``."""
    overrides = current_config().mck_overrides.values
    raw = {
        "gap_threshold": overrides.get("gap_threshold"),
        "enclosure_expand_max": overrides.get("enclosure_expand_max"),
        "default_search_cutoff": overrides.get("default_search_cutoff"),
    }
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        params = {}
    return {
        key: value
        for key, value in raw.items()
        if value is not None and (not params or key in params)
    }


def classify_fragments(bundle) -> list[dict[str, Any]]:
    return list(
        getattr(bundle, "topology_fragment_table", None) or bundle.fragment_table
    )


def _shift_hull_payload(
    hull: dict[str, Any] | None, delta: np.ndarray
) -> dict[str, Any]:
    """Return a copy of a MolCrysKit hull payload translated by ``delta``."""
    if not hull:
        return {"vertices": [], "simplices": [], "edges": []}
    out = dict(hull)
    vertices = hull.get("vertices") or []
    arr = np.asarray(vertices, dtype=float) if len(vertices) else np.empty((0, 3))
    if arr.size > 0 and arr.ndim == 2 and arr.shape[1] == 3:
        out["vertices"] = (arr + delta).tolist()
    else:
        out["vertices"] = []
    return out


def _validated_mck_molecule_index(bundle, crystal, source_index: int) -> int:
    """Validate the MatterVis/MolCrysKit molecule-index bridge.

    Fragment rows carry the index used by ``CrystalAnalysis.mol_indices``.
    Before using that value to index ``crystal.molecules``, compare the
    molecule's global atom membership.  This turns a possible silent formula
    mismatch after a reorder into an actionable error.
    """
    analysis = getattr(bundle, "molcrys_analysis", None)
    groups = getattr(analysis, "mol_indices", None)
    molecules = getattr(crystal, "molecules", None)
    if not groups or molecules is None:
        return int(source_index)
    source_index = int(source_index)
    if not 0 <= source_index < len(groups):
        raise ValueError(
            f"MolCrysKit source molecule index {source_index} is outside "
            f"mol_indices ({len(groups)} groups)."
        )

    records_by_molecule: dict[int, set[int]] = {}
    try:
        for record in crystal.get_site_records():
            records_by_molecule.setdefault(int(record.molecule_index), set()).add(
                int(record.global_index)
            )
    except (AttributeError, TypeError, ValueError):
        records_by_molecule = {}

    target = {int(index) for index in groups[source_index]}
    matches: list[int] = []
    for molecule_index, molecule in enumerate(molecules):
        info = getattr(molecule, "info", {}) or {}
        members = info.get("atom_indices")
        if members is None:
            members = records_by_molecule.get(molecule_index)
        if members is None:
            continue
        if {int(index) for index in members} == target:
            matches.append(molecule_index)
    if len(matches) != 1:
        raise ValueError(
            "Cannot verify MolCrysKit molecule ordering for source index "
            f"{source_index}: global atom membership matched {matches}."
        )
    if matches[0] != source_index:
        raise ValueError(
            "MolCrysKit molecule ordering differs from MatterVis mol_indices: "
            f"source {source_index} maps to crystal molecule {matches[0]}."
        )
    return source_index


def _as_loaded_molecule_formula(molecule) -> str:
    """Formula prefix of MolCrysKit's topology signature for ``molecule``."""

    return str(compute_topo_signature(molecule).split("|", 1)[0])


def extract_atom_coordination_shells(
    bundle,
    cutoff: float | None,
    *,
    center_species: str,
    ligand_species: str,
    source_indices: Iterable[int] | None = None,
    enforce_enclosure: bool = True,
    centroid_offset_frac: float = DEFAULT_CENTROID_OFFSET_FRAC,
    fallback_max: int | None = None,
    disorder_policy: str = DEFAULT_DISORDER_POLICY,
) -> dict[int, dict[str, Any]]:
    """Return atom-level shells keyed by raw crystallographic atom index."""
    ligand_formula = _ligand_formula_for_molcryskit(ligand_species)
    crystal = molcrys_bridge.molecular_crystal_from_bundle(bundle)
    public_module = sys.modules.get("mat_viewer.topology")
    find_polyhedra_impl = getattr(public_module, "find_polyhedra", find_polyhedra)
    atom_kwargs: dict[str, Any] = {}
    selected_indices = None
    if source_indices is not None:
        selected_indices = sorted({int(index) for index in source_indices})
    if cutoff is not None:
        atom_kwargs["cutoff"] = float(cutoff)
    if fallback_max is not None:
        atom_kwargs["fallback_max"] = int(fallback_max)
    records = invoke_find_polyhedra(
        find_polyhedra_impl,
        crystal,
        str(center_species),
        ligand_formula,
        level="atom",
        disorder_policy=disorder_policy,
        central_indices=selected_indices,
        enforce_enclosure=bool(enforce_enclosure),
        centroid_offset_frac=float(centroid_offset_frac),
        **atom_kwargs,
        **_mck_override_kwargs(find_polyhedra_impl),
    )
    shells: dict[int, dict[str, Any]] = {}
    for record in records:
        source_index = int(record["center_index"])
        center = np.asarray(record["center_position"], dtype=float)
        shell_coords = np.asarray(record.get("shell_coords") or [], dtype=float)
        if shell_coords.size == 0:
            shell_coords = np.zeros((0, 3), dtype=float)
        shells[source_index] = {
            "center_source_index": source_index,
            "source_center_coords": center.tolist(),
            "source_shell_coords": shell_coords.tolist(),
            "distances": [
                float(value) for value in record.get("shell_distances") or []
            ],
            "source_hull": convex_hull_payload(shell_coords),
        }
    return shells


def display_atom_centers_for_spec(
    bundle,
    scene: dict[str, Any],
    spec: dict[str, Any],
    *,
    source_indices: Iterable[int] | None = None,
    include_images: bool = False,
) -> list[dict[str, Any]]:
    """Return displayed atom centres with stable source/image identity.

    The default is one centre per source atom in the primary half-open cell.
    Boundary-image centres are opt-in; their ligand shells may still cross PBC.
    """
    from ..scene.core import source_image_identity

    wanted = str(spec.get("center_species") or "")
    allowed = (
        {int(index) for index in source_indices} if source_indices is not None else None
    )
    source_atoms = list(bundle.raw_atoms)
    seen: set[tuple[int, tuple[int, int, int]]] = set()
    centers: list[dict[str, Any]] = []
    for draw_index, atom in enumerate(scene.get("draw_atoms") or []):
        if str(atom.get("elem") or "") != wanted:
            continue
        identity = source_image_identity(atom, source_atoms, draw_index)
        if identity is None or identity in seen:
            continue
        if not include_images and identity[1] != (0, 0, 0):
            continue
        if allowed is not None and identity[0] not in allowed:
            continue
        seen.add(identity)
        centers.append(
            {
                "draw_index": draw_index,
                "source_index": identity[0],
                "image": identity[1],
                "label": atom.get("label") or f"{wanted}{identity[0]}",
                "center": np.asarray(atom.get("cart"), dtype=float).tolist(),
                "color": atom.get("color") or element_color(wanted),
            }
        )
    return centers


def atom_overlay(shell: dict[str, Any], center: dict[str, Any]) -> dict[str, Any]:
    """Translate one source atom shell onto a displayed periodic image."""
    source_center = np.asarray(shell["source_center_coords"], dtype=float)
    display_center = np.asarray(center["center"], dtype=float)
    delta = display_center - source_center
    source_shell = np.asarray(shell.get("source_shell_coords") or [], dtype=float)
    shell_coords = (
        source_shell + delta if len(source_shell) else np.zeros((0, 3), dtype=float)
    )
    source_hull = shell.get("source_hull") or {}
    hull = dict(source_hull)
    source_vertices = np.asarray(source_hull.get("vertices") or [], dtype=float)
    if source_vertices.ndim == 2 and source_vertices.shape[1:] == (3,):
        hull["vertices"] = (source_vertices + delta).tolist()
    else:
        hull["vertices"] = []
    return {
        "center_coords": display_center.tolist(),
        "center_label": center["label"],
        "center_source_index": center["source_index"],
        "center_image": list(center["image"]),
        "center_draw_index": center["draw_index"],
        "shell_coords": shell_coords.tolist(),
        "distances": shell.get("distances") or [],
        "hull": hull,
        "color": center.get("color"),
        "is_analysis_anchor": False,
    }


def _extract_coordination_shell_static(
    bundle,
    center_index: int,
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
) -> dict[str, Any]:
    disorder_policy = normalize_disorder_policy(disorder_policy)
    fragments = classify_fragments(bundle)
    center_fragment = next(
        (frag for frag in fragments if int(frag["index"]) == int(center_index)), None
    )
    if center_fragment is None:
        raise IndexError(f"Unknown fragment index: {center_index}")
    record = _mck_polyhedron_record(
        bundle,
        center_fragment,
        cutoff,
        ligand_species=ligand_species,
        level=level,
        center_species=center_species,
        enforce_enclosure=enforce_enclosure,
        centroid_offset_frac=centroid_offset_frac,
        center_kind=center_kind,
        hard_cutoff=hard_cutoff,
        fallback_max=fallback_max,
        disorder_policy=disorder_policy,
    )
    if record is None:
        source_center = np.array(center_fragment["center"], dtype=float)
        source_shell_coords = np.zeros((0, 3), dtype=float)
        shell_distances: list[float] = []
        hull = convex_hull_payload(source_shell_coords)
        cn = 0
        # Empty-shell fallback: report the search radius MV asked for so
        # the analysis card can still render "search_cutoff: 10 Å, no
        # neighbours found" instead of an entirely blank row.
        gap_info: dict[str, Any] = {
            "coordination_number": 0,
            "mode": level,
            "primary_gap_cn": 0,
            "gap_index": None,
            "gap_value": None,
            "enclosed": False,
            "enclosure_expanded": False,
            "cutoff": None,
            "search_cutoff": float(cutoff),
            "hard_cutoff": None,
            "disorder_policy": disorder_policy,
            "rejection_reason": "no_shell",
        }
        extra_shells: list[dict[str, Any]] = []
    else:
        source_center = np.array(record["center_position"], dtype=float)
        source_shell_coords = np.array(record.get("shell_coords") or [], dtype=float)
        if source_shell_coords.size == 0:
            source_shell_coords = np.zeros((0, 3), dtype=float)
        shell_distances = [float(x) for x in record.get("shell_distances") or []]
        hull = convex_hull_payload(source_shell_coords)
        cn = int(record.get("coordination_number", len(shell_distances)))
        # MCK PR #32 split the radial knobs at molecule level:
        # * ``record["search_cutoff"]`` is the candidate search radius
        #   actually used (= MV's state ``cutoff``).
        # * ``record["hard_cutoff"]`` is None when the natural-shell
        #   gap+enclosure path ran (the MV default), or a float when the
        #   caller explicitly opted into the "fill the ball" mode.
        # * ``record["cutoff"]`` echoes whatever ``detect_coordination_
        #   number`` received (= ``hard_cutoff`` value, or None). Kept
        #   here for back-compat but downstream code that wants to ask
        #   "was a hard cap applied?" should read ``hard_cutoff``.
        gap_info = {
            "coordination_number": cn,
            "mode": record.get("mode"),
            "primary_gap_cn": record.get("primary_gap_cn"),
            "gap_index": record.get("gap_index"),
            "gap_value": record.get("gap_value"),
            "enclosed": record.get("enclosed"),
            "enclosure_expanded": record.get("enclosure_expanded"),
            "cutoff": record.get("cutoff"),
            "search_cutoff": record.get("search_cutoff"),
            "hard_cutoff": record.get("hard_cutoff"),
            "disorder_policy": disorder_policy,
            "rejection_reason": record.get("rejection_reason"),
        }
        extra_shells = []
        for extra in record.get("extra_shells") or []:
            coords = np.asarray(extra.get("shell_coords") or [], dtype=float)
            if coords.size == 0:
                coords = np.zeros((0, 3), dtype=float)
            extra_shells.append(
                {
                    "source_center_coords": np.asarray(
                        extra.get("center_position") or source_center, dtype=float
                    ),
                    "source_shell_coords": coords,
                    "distances": [
                        float(value) for value in extra.get("shell_distances") or []
                    ],
                    "source_hull": convex_hull_payload(coords),
                }
            )
    # MolCrysKit reports shell members by source molecule index.  The
    # analysis card needs the stable MatterVis fragment identity as well;
    # leaving this translation to the renderer produced ``?(?)`` labels and
    # made it impossible to tell an N centre from a ClO4 neighbour.  Keep the
    # MCK index for callers that use it as a chemistry key, then add the
    # corresponding fragment label/formula when the bundle carries the
    # canonical topology table.
    fragments_by_source_molecule = {
        int(fragment["source_molecule_index"]): fragment
        for fragment in fragments
        if fragment.get("source_molecule_index") is not None
    }
    shell_indices = (
        (record or {}).get("shell_molecule_indices")
        or (record or {}).get("shell_indices")
        or []
    )
    shell_offsets = (record or {}).get("shell_offsets") or []
    shell = []
    for idx, coord, dist, offset in zip(
        shell_indices,
        source_shell_coords.tolist(),
        shell_distances,
        shell_offsets,
    ):
        fragment = fragments_by_source_molecule.get(int(idx))
        raw_atom = None
        if fragment is None:
            try:
                raw_atom = bundle.raw_atoms[int(idx)]
            except (AttributeError, IndexError, TypeError, ValueError):
                raw_atom = None
        shell.append(
            {
                "index": int(idx),
                "fragment_index": (
                    None if fragment is None else int(fragment["index"])
                ),
                "label": (
                    str(raw_atom.get("label") or f"site-{int(idx)}")
                    if fragment is None and raw_atom is not None
                    else (
                        f"site-{int(idx)}"
                        if fragment is None
                        else str(fragment.get("label") or f"site-{int(idx)}")
                    )
                ),
                "species": (
                    str(raw_atom.get("elem") or "?")
                    if fragment is None and raw_atom is not None
                    else (
                        "?"
                        if fragment is None
                        else str(fragment.get("formula") or fragment.get("species") or "?")
                    )
                ),
                "center": coord,
                "distance": dist,
                "image_shift": offset,
            }
        )
    return {
        "center_index": int(center_index),
        "default_label": center_fragment.get("label", f"site-{center_index}"),
        "default_type": center_fragment.get("type", "?"),
        "center_formula": center_fragment.get("formula")
        or center_fragment.get("species"),
        "analysis_level": level,
        "source_center_coords": source_center,
        "cutoff": float(cutoff),
        "neighbor_pool_size": len(shell),
        "coordination_number": cn,
        "gap_info": gap_info,
        "shell": shell,
        "candidate_fragments": shell,
        "source_shell_coords": source_shell_coords,
        "source_hull": hull,
        "distances": shell_distances,
        "all_distances": shell_distances,
        "extra_shells": extra_shells,
    }


def extract_coordination_shell(
    bundle,
    center_index: int,
    cutoff: float = 10.0,
    *,
    display_center: Iterable[float] | None = None,
    display_label: str | None = None,
    display_type: str | None = None,
    ligand_species: Iterable[str] | None = None,
    level: str = "molecule",
    center_species: str | None = None,
    enforce_enclosure: bool = True,
    centroid_offset_frac: float = DEFAULT_CENTROID_OFFSET_FRAC,
    center_kind: str = "centroid",
    hard_cutoff: float | None = None,
    fallback_max: int | None = None,
    disorder_policy: str = DEFAULT_DISORDER_POLICY,
) -> dict[str, Any]:
    ligand_tuple = _query_ligand_species(ligand_species)
    static = _extract_coordination_shell_static(
        bundle,
        int(center_index),
        float(cutoff),
        ligand_species=ligand_tuple,
        level=level,
        center_species=center_species,
        enforce_enclosure=enforce_enclosure,
        centroid_offset_frac=centroid_offset_frac,
        center_kind=center_kind,
        hard_cutoff=hard_cutoff,
        fallback_max=fallback_max,
        disorder_policy=disorder_policy,
    )
    source_center = np.asarray(static["source_center_coords"], dtype=float)
    plot_center = (
        source_center
        if display_center is None
        else np.array(display_center, dtype=float)
    )
    delta = plot_center - source_center

    source_shell_coords = np.asarray(static["source_shell_coords"], dtype=float)
    shell_coords = (
        source_shell_coords + delta
        if len(source_shell_coords)
        else np.zeros((0, 3), dtype=float)
    )
    candidates = static["candidate_fragments"]
    pool_coords_arr = (
        np.array([item["center"] for item in candidates], dtype=float) + delta
        if candidates
        else np.zeros((0, 3), dtype=float)
    )
    hull = _shift_hull_payload(static.get("source_hull"), delta)
    extra_shells = []
    for extra in static.get("extra_shells") or []:
        src_center = np.asarray(extra["source_center_coords"], dtype=float)
        src_shell = np.asarray(extra["source_shell_coords"], dtype=float)
        extra_shells.append(
            {
                "source_center_coords": src_center,
                "source_shell_coords": src_shell,
                "distances": extra.get("distances") or [],
                "source_hull": extra.get("source_hull"),
                "center_coords": (src_center + delta).tolist(),
                "shell_coords": (src_shell + delta).tolist() if src_shell.size else [],
                "hull": _shift_hull_payload(
                    extra.get("source_hull") or extra.get("hull"), delta
                ),
            }
        )
    return {
        "center_index": int(center_index),
        "center_label": display_label or static["default_label"],
        "center_type": display_type or static["default_type"],
        "center_formula": static["center_formula"],
        "analysis_level": static["analysis_level"],
        "center_coords": plot_center.tolist(),
        "source_center_coords": source_center.tolist(),
        "cutoff": float(cutoff),
        "neighbor_pool_size": static["neighbor_pool_size"],
        "coordination_number": static["coordination_number"],
        "gap_info": static["gap_info"],
        "shell": static["shell"],
        "candidate_fragments": candidates,
        "shell_coords": shell_coords.tolist(),
        "source_shell_coords": source_shell_coords.tolist(),
        "hull": hull,
        "distances": static["distances"],
        "all_distances": static["all_distances"],
        "pool_coords": pool_coords_arr.tolist(),
        "extra_shells": extra_shells,
    }


def _analyze_topology_uncached(
    bundle,
    center_index: int,
    cutoff: float,
    display_center,
    display_label,
    display_type,
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
) -> dict[str, Any]:
    shell = extract_coordination_shell(
        bundle,
        center_index=center_index,
        cutoff=cutoff,
        display_center=display_center,
        display_label=display_label,
        display_type=display_type,
        ligand_species=ligand_species,
        level=level,
        center_species=center_species,
        enforce_enclosure=enforce_enclosure,
        centroid_offset_frac=centroid_offset_frac,
        center_kind=center_kind,
        hard_cutoff=hard_cutoff,
        fallback_max=fallback_max,
        disorder_policy=disorder_policy,
    )
    center = shell["center_coords"]
    shell_coords = shell["shell_coords"]
    # Use molcrys_kit's modern CShM-based classifier (classify_shell) instead
    # of the deprecated angular_rmsd_vs_ideals. classify_shell returns clean
    # labels like "irregular cuboctahedron" with a structural description and
    # core/residual decomposition; the old angular signature returned vague
    # "best ideal" tokens that confused chemists looking at packing shells
    # around organic cations. ``max_strip=1`` and ``n_random_inits=4`` keep
    # the per-call cost under ~200 ms for CN<=12; the result is bundle-cached
    # via ``_analyze_topology_cache`` so it only runs once per (centre,
    # cutoff, ligand) tuple.
    public_module = sys.modules.get("mat_viewer.topology")
    classify_shell_payload_impl = getattr(
        public_module, "_classify_shell_payload", _classify_shell_payload
    )
    shape = classify_shell_payload_impl(shell_coords, center)
    # The diagnostics panel also asks MolCrysKit for a best planar subset;
    # that helper enumerates C(N, 5) combinations. Reuse the same bounded
    # nearest-neighbour cage for oversized periodic shells so the complete
    # topology request remains finite after shape classification returns.
    diagnostics = shape.get("diagnostics") if isinstance(shape, dict) else None
    sample_indices = diagnostics.get("sample_indices") if isinstance(diagnostics, dict) else None
    if sample_indices:
        diagnostic_coords = [shell_coords[int(index)] for index in sample_indices]
    else:
        diagnostic_coords = shell_coords
    planarity = planarity_analysis(
        diagnostic_coords,
        group_size=min(5, len(diagnostic_coords)) if len(diagnostic_coords) else 5,
    )
    prism = detect_prism_vs_antiprism(diagnostic_coords)
    return {
        **shell,
        "shape": shape,
        "analysis_level": level,
        "packing_shell_label": (
            shape.get("primary_label") if level == "molecule" else None
        ),
        "coordination_polyhedron_label": (
            shape.get("primary_label") if level == "atom" else None
        ),
        "planarity": planarity,
        "prism_analysis": prism,
    }




_MAX_REGISTERED_CN = max(int(cn) for cn in all_ideal_polyhedra())


def _classify_shell_payload(
    shell_coords: list | np.ndarray,
    center: list | np.ndarray,
) -> dict[str, Any]:
    """Run ``classify_shell`` defensively and return a JSON-safe payload.

    ``classify_shell`` raises on degenerate inputs (CN < 1, all points
    collinear, etc.) which we don't want to bubble up to the renderer
    text-panel call site; an empty / partial shell should just degrade to
    ``primary_label = None`` rather than crash the whole topology card.
    """
    if len(np.asarray(shell_coords)) == 0:
        return _empty_shape_payload()
    coords_arr = np.asarray(shell_coords, dtype=float)
    classification_coords = coords_arr
    bounded_sample = False
    sample_indices: list[int] = []
    if len(coords_arr) > _MAX_REGISTERED_CN:
        # A shell can contain many periodic image neighbours when the source
        # cell is small.  The registered shape catalogue cannot describe an
        # arbitrary CN, and classify_shell's residual decomposition would
        # enumerate C(N, k) subsets.  Fit the nearest registered-size local
        # cage with the rigid path instead; retain the true CN below so the
        # caller can distinguish the approximation from a complete shell.
        center_arr = np.asarray(center, dtype=float)
        distances = np.linalg.norm(coords_arr - center_arr, axis=1)
        sample_indices = np.argsort(distances, kind="mergesort")[
            :_MAX_REGISTERED_CN
        ].astype(int).tolist()
        classification_coords = coords_arr[sample_indices]
        bounded_sample = True
    try:
        # Delegate the shell label policy to molcrys_kit's current
        # classifier. Earlier MatterVis pinned ``max_strip=0`` to force a
        # rigid CN=N CShM match, but that bypassed MCK's newer core/residual
        # decomposition and mislabelled registered derived shells such as
        # EAP-4 AX11 (tricapped cube) as rigid CN=11 alternatives. Leaving
        # ``max_strip`` unset keeps MV aligned with upstream MCK labels while
        # still returning a JSON-safe payload for the renderer/scripts.
        result = classify_shell(
            classification_coords,
            center=center,
            n_random_inits=4,
            top_k=3,
            max_strip=0 if bounded_sample else None,
        )
    except Exception as exc:
        return {**_empty_shape_payload(), "error": str(exc)}
    payload = _sanitize_shape_payload(result)
    if bounded_sample:
        payload["coordination_number"] = len(coords_arr)
        payload["diagnostics"] = {
            **(payload.get("diagnostics") or {}),
            "search_mode": "bounded_local_cage",
            "sample_size": len(classification_coords),
            "sample_indices": sample_indices,
            "max_registered_cn": _MAX_REGISTERED_CN,
        }
    return payload


# Fields under ``shape["topology"]`` (and inside each candidate's
# ``core.topology``) carry molcrys_kit's polyhedron-registry namedtuples
# (``FaceInfo``, ``EdgeInfo``, etc.) that are not JSON-serialisable and
# not actionable for any downstream consumer in this repo. Strip them so
# ``analyze_topology`` keeps its "JSON-safe payload" contract.
_SHAPE_DROP_KEYS = ("topology",)


def _sanitize_shape_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = {
        key: value for key, value in payload.items() if key not in _SHAPE_DROP_KEYS
    }
    if isinstance(cleaned.get("core"), dict):
        cleaned["core"] = {
            key: value
            for key, value in cleaned["core"].items()
            if key not in _SHAPE_DROP_KEYS
        }
    cleaned["candidates"] = [
        _sanitize_candidate(item) for item in cleaned.get("candidates") or []
    ]
    cleaned["alternatives"] = [
        _sanitize_candidate(item) for item in cleaned.get("alternatives") or []
    ]
    if isinstance(cleaned.get("best_match"), dict):
        cleaned["best_match"] = _sanitize_candidate(cleaned["best_match"])
    return cleaned


def _sanitize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    cleaned = {
        key: value for key, value in candidate.items() if key not in _SHAPE_DROP_KEYS
    }
    if isinstance(cleaned.get("core"), dict):
        cleaned["core"] = {
            key: value
            for key, value in cleaned["core"].items()
            if key not in _SHAPE_DROP_KEYS
        }
    return cleaned


def _empty_shape_payload() -> dict[str, Any]:
    return {
        "coordination_number": 0,
        "primary_label": None,
        "label_modifier": None,
        "label_source": None,
        "confidence_gap": None,
        "cshm_value": None,
        "core": None,
        "residuals": [],
        "structural_description": "",
        "alternatives": [],
        "best_match": None,
        "candidates": [],
    }


def analyze_topology(
    bundle,
    center_index: int,
    cutoff: float = 10.0,
    *,
    display_center: Iterable[float] | None = None,
    display_label: str | None = None,
    display_type: str | None = None,
    ligand_species: Iterable[str] | None = None,
    level: str = "molecule",
    center_species: str | None = None,
    enforce_enclosure: bool = True,
    centroid_offset_frac: float = DEFAULT_CENTROID_OFFSET_FRAC,
    center_kind: str = "centroid",
    hard_cutoff: float | None = None,
    fallback_max: int | None = None,
    disorder_policy: str = DEFAULT_DISORDER_POLICY,
) -> dict[str, Any]:
    """Cached primary-site analysis. The heavy ``planarity_analysis`` pass
    runs ``itertools.combinations`` of size 5 over the shell, which gets
    expensive for CN=12 / large neighbour pools. We key the cache on
    ``(center_index, cutoff, ligand_species, packing-shell knobs)`` -- the
    full bundle topology is immutable once loaded -- so flipping species
    checkboxes back and forth no longer redoes the work, but two named
    polyhedron specs with different ligand restrictions get distinct cache
    slots. PBC image enumeration is delegated to MolCrysKit's
    ``find_polyhedra(level="molecule")`` implementation.
    """
    ligand_tuple = _query_ligand_species(ligand_species)
    level = str(level or "molecule")
    center_kind = str(center_kind or "centroid")
    hard_cap = float(hard_cutoff) if hard_cutoff is not None else None
    fallback = int(fallback_max) if fallback_max is not None else None
    disorder_policy = normalize_disorder_policy(disorder_policy)
    cache = getattr(bundle, "_analyze_topology_cache", None)
    if cache is None:
        cache = {}
        try:
            bundle._analyze_topology_cache = cache
        except Exception:
            return _analyze_topology_uncached(
                bundle,
                center_index,
                cutoff,
                display_center,
                display_label,
                display_type,
                ligand_species=ligand_tuple,
                level=level,
                center_species=center_species,
                enforce_enclosure=enforce_enclosure,
                centroid_offset_frac=centroid_offset_frac,
                center_kind=center_kind,
                hard_cutoff=hard_cap,
                fallback_max=fallback,
                disorder_policy=disorder_policy,
            )
    key = (
        int(center_index),
        float(cutoff),
        ligand_tuple,
        level,
        center_species,
        bool(enforce_enclosure),
        float(centroid_offset_frac),
        # MCK 0.4 knobs participate in the cache key because each one
        # changes the chosen shell (and therefore the shape classification).
        center_kind,
        hard_cap,
        fallback,
        disorder_policy,
    )
    cached = cache.get(key)
    if cached is None:
        cached = _analyze_topology_uncached(
            bundle,
            center_index,
            cutoff,
            None,
            None,
            None,
            ligand_species=ligand_tuple,
            level=level,
            center_species=center_species,
            enforce_enclosure=enforce_enclosure,
            centroid_offset_frac=centroid_offset_frac,
            center_kind=center_kind,
            hard_cutoff=hard_cap,
            fallback_max=fallback,
            disorder_policy=disorder_policy,
        )
        cache[key] = cached
    # Display fields shift per call (camera / formula-unit centering); patch
    # them onto a shallow copy so the cache stays generic.
    out = dict(cached)
    if display_center is not None:
        plot_center = np.array(display_center, dtype=float)
        source_center = np.array(
            out.get("source_center_coords", plot_center), dtype=float
        )
        delta = plot_center - source_center
        out["center_coords"] = plot_center.tolist()
        if out.get("source_shell_coords"):
            shell = np.array(out["source_shell_coords"], dtype=float) + delta
            out["shell_coords"] = shell.tolist()
        out["hull"] = _shift_hull_payload(
            out.get("source_hull") or out.get("hull"), delta
        )
        shifted = []
        for extra in out.get("extra_shells") or ():
            src_center = np.asarray(extra["source_center_coords"], dtype=float)
            src_shell = np.asarray(extra["source_shell_coords"], dtype=float)
            shifted.append(
                {
                    **extra,
                    "center_coords": (src_center + delta).tolist(),
                    "shell_coords": (
                        (src_shell + delta).tolist() if src_shell.size else []
                    ),
                    "hull": _shift_hull_payload(
                        extra.get("source_hull") or extra.get("hull"), delta
                    ),
                }
            )
        out["extra_shells"] = shifted
    if display_label is not None:
        out["center_label"] = display_label
    if display_type is not None:
        out["center_type"] = display_type
    return out
