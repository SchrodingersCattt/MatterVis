from __future__ import annotations

import inspect
import sys
from typing import Any, Iterable

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
from molcrys_kit.structures.polyhedra import convex_hull_payload, ideal_polyhedra_for_cn

from ..config import current_config, element_color
from ..structure import molcrys_bridge

__all__ = [
    "DEFAULT_CENTROID_OFFSET_FRAC",
    "_hull_encloses_center",
    "atom_overlay",
    "display_atom_centers_for_spec",
    "analyze_topology",
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
) -> dict[str, Any] | None:
    if not ligand_species:
        raise ValueError(
            "MolCrysKit polyhedra require an explicit ligand_species; "
            "MatterVis no longer derives ligand shells locally."
        )
    ligand_formula = next((str(item) for item in ligand_species if item), None)
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
        find_polyhedra_impl = getattr(public_module, "find_polyhedra", find_polyhedra)
        # ``hard_cutoff`` is rejected at atom level by MCK: ``cutoff=`` is
        # already the hard radial cap on this level. The normaliser
        # already drops ``hard_cutoff`` whenever level=='atom'; defend
        # here too so a direct caller (script / REST) can't break MCK.
        atom_kwargs: dict[str, Any] = {}
        if fallback_max is not None:
            atom_kwargs["fallback_max"] = int(fallback_max)
        records = find_polyhedra_impl(
            crystal,
            central_symbol,
            ligand_formula,
            level="atom",
            cutoff=float(cutoff),
            enforce_enclosure=bool(enforce_enclosure),
            centroid_offset_frac=float(centroid_offset_frac),
            **atom_kwargs,
            **_mck_override_kwargs(find_polyhedra_impl),
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
        molecule_index = _validated_mck_molecule_index(
            bundle, crystal, molecule_index
        )
        center_formula = compute_topo_signature(
            molecules[molecule_index]
        ).split("|", 1)[0]
    else:
        # Compatibility for lightweight bridge objects and third-party loaders.
        center_formula = str(display_formula)
    # On level="molecule", MCK's ``cutoff`` IS the candidate search radius
    # that feeds gap+enclosure (per MCK PR #32). MV's state-level ``cutoff``
    # is the search radius too, so the kwarg name lines up after the MCK
    # split. ``hard_cutoff`` is opt-in per-spec for the historical
    # "fill the ball" mode (CN=12 cuboctahedron on the SY perchlorate, for
    # example); leaving it ``None`` keeps the natural first-shell answer.
    public_module = sys.modules.get("mat_viewer.topology")
    find_polyhedra_impl = getattr(public_module, "find_polyhedra", find_polyhedra)
    extra_kwargs: dict[str, Any] = {}
    if hard_cutoff is not None:
        extra_kwargs["hard_cutoff"] = float(hard_cutoff)
    if fallback_max is not None:
        extra_kwargs["fallback_max"] = int(fallback_max)
    records = find_polyhedra_impl(
        crystal,
        molcrys_bridge.formula_to_moiety(str(center_formula)),
        molcrys_bridge.formula_to_moiety(ligand_formula),
        level="molecule",
        center_kind=str(center_kind or "centroid"),
        cutoff=float(cutoff),
        central_indices=[int(molecule_index)],
        enforce_enclosure=bool(enforce_enclosure),
        centroid_offset_frac=float(centroid_offset_frac),
        **extra_kwargs,
        **_mck_override_kwargs(find_polyhedra_impl),
    )
    return records[0] if records else None


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
) -> dict[int, dict[str, Any]]:
    """Return atom-level shells keyed by raw crystallographic atom index."""
    crystal = molcrys_bridge.molecular_crystal_from_bundle(bundle)
    public_module = sys.modules.get("mat_viewer.topology")
    find_polyhedra_impl = getattr(public_module, "find_polyhedra", find_polyhedra)
    atom_kwargs: dict[str, Any] = {}
    if source_indices is not None:
        atom_kwargs["central_indices"] = sorted(
            {int(index) for index in source_indices}
        )
    if cutoff is not None:
        atom_kwargs["cutoff"] = float(cutoff)
    if fallback_max is not None:
        atom_kwargs["fallback_max"] = int(fallback_max)
    records = find_polyhedra_impl(
        crystal,
        str(center_species),
        str(ligand_species),
        level="atom",
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
    hull["vertices"] = (
        (source_vertices + delta).tolist() if len(source_vertices) else []
    )
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
) -> dict[str, Any]:
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
        }
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
        }
    shell = [
        {
            "index": int(idx),
            "center": coord,
            "distance": dist,
            "image_shift": offset,
        }
        for idx, coord, dist, offset in zip(
            (record or {}).get("shell_molecule_indices")
            or (record or {}).get("shell_indices")
            or [],
            source_shell_coords.tolist(),
            shell_distances,
            (record or {}).get("shell_offsets") or [],
        )
    ]
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
) -> dict[str, Any]:
    ligand_tuple = (
        tuple(str(item) for item in ligand_species) if ligand_species else None
    )
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
    planarity = planarity_analysis(
        shell_coords, group_size=min(5, len(shell_coords)) if shell_coords else 5
    )
    prism = detect_prism_vs_antiprism(shell_coords)
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
    try:
        # Delegate the shell label policy to molcrys_kit's current
        # classifier. Earlier MatterVis pinned ``max_strip=0`` to force a
        # rigid CN=N CShM match, but that bypassed MCK's newer core/residual
        # decomposition and mislabelled registered derived shells such as
        # EAP-4 AX11 (tricapped cube) as rigid CN=11 alternatives. Leaving
        # ``max_strip`` unset keeps MV aligned with upstream MCK labels while
        # still returning a JSON-safe payload for the renderer/scripts.
        result = classify_shell(
            shell_coords,
            center=center,
            n_random_inits=4,
            top_k=3,
        )
    except Exception as exc:
        return {**_empty_shape_payload(), "error": str(exc)}
    return _sanitize_shape_payload(result)


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
    ligand_tuple = (
        tuple(str(item) for item in ligand_species) if ligand_species else None
    )
    level = str(level or "molecule")
    center_kind = str(center_kind or "centroid")
    hard_cap = float(hard_cutoff) if hard_cutoff is not None else None
    fallback = int(fallback_max) if fallback_max is not None else None
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
    if display_label is not None:
        out["center_label"] = display_label
    if display_type is not None:
        out["center_type"] = display_type
    return out
