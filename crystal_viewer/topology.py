from __future__ import annotations

import itertools
import math
from typing import Any, Iterable

import numpy as np

from molcrys_kit.analysis.packing_shell import (
    DEFAULT_CENTROID_OFFSET_FRAC,
    angular_rmsd_vs_ideals,
    compute_angular_signature,
    detect_coordination_number,
    detect_prism_vs_antiprism,
    find_polyhedra,
    hull_encloses_center as _hull_encloses_center,
    planarity_analysis,
)
from molcrys_kit.structures.polyhedra import convex_hull_payload, ideal_polyhedra_for_cn

__all__ = [
    "DEFAULT_CENTROID_OFFSET_FRAC",
    "_hull_encloses_center",
    "analyze_topology",
    "angular_rmsd_vs_ideals",
    "atom_centered_polyhedra",
    "classify_fragments",
    "compute_angular_signature",
    "convex_hull_payload",
    "detect_coordination_number",
    "detect_prism_vs_antiprism",
    "extract_coordination_shell",
    "find_polyhedra",
    "ideal_polyhedra_for_cn",
    "planarity_analysis",
    "suggest_default_polyhedron_specs",
]


# ---------------------------------------------------------------------------
# Atom-centered polyhedra (molcrys_kit driven)
# ---------------------------------------------------------------------------
#
# The legacy ``polyhedron_specs`` model is fragment-centric: each spec picks a
# *fragment* formula as the centre (e.g. ``NH4``) and looks for surrounding
# *fragments* of another formula (e.g. ``ClO4``) as ligands. That works well
# for true coordination chemistry (A-site cation surrounded by anion units in
# a perovskite framework) but produces nonsensical "ambiguous cuboctahedra"
# when the chemistry is really atom-level (perchlorate's Cl is tetrahedrally
# coordinated by O atoms, not "surrounded by other ClO4 fragments").
#
# Atom-centered specs (``kind="atom"``) bypass the fragment graph entirely:
# they call MolCrysKit's :func:`find_polyhedra` (ASE neighbour-list driven,
# PBC aware, picks coordination number via gap+enclosure) on raw atoms. The
# result -- one Cartesian shell per central atom -- is converted to the same
# overlay format the renderer already consumes for fragment-centred specs.
#
# Defaults for new structures are now atom-centered: see
# :func:`suggest_default_polyhedron_specs` for the heuristic.

# Elements that are textbook "central atoms" of tetrahedral / octahedral
# anions when paired with O (perchlorate, sulfate, nitrate, phosphate,
# silicate, vanadate, tungstate, etc.). Each entry is
# ``(central, ligand, max_bond_A)`` where ``max_bond_A`` is a tight
# COVALENT-bond cap (well below H-bond / van-der-Waals contacts) used
# both to gate the suggestion (only fire when at least one centre has
# its full coordination sphere within the cap) AND to feed MolCrysKit's
# ``search_cutoff`` so the polyhedron we draw is the textbook one.
#
# Without the cap, organic-cation N atoms in perchlorate hybrids would
# get surrounded by far-away ClO4 oxygens (3-5 A) and produce huge
# cuboctahedra. With the cap, only chemistry-meaningful covalent
# polyhedra (NO3 at ~1.25 A, ClO4 at ~1.45 A, PbCl6 at ~3.0 A) are
# suggested.
_ATOM_POLY_DEFAULT_CHEMISTRY: tuple[tuple[str, str, float], ...] = (
    # Tetrahedral / trigonal-pyramidal anion centers (X-O ~ 1.2-1.7 A).
    # Use 2.0 A so PVT / strained variants still register but H-bonded
    # neighbours are firmly excluded.
    ("Cl", "O", 2.0),  # ClO4-, ClO3-
    ("Br", "O", 2.0),  # BrO3-
    ("I", "O", 2.2),   # IO3-, IO4-
    ("S", "O", 1.9),   # SO4-2, SO3-2
    ("P", "O", 1.9),   # PO4-3
    ("N", "O", 1.6),   # NO3-, NO2-: N=O ~ 1.25 A; cap below H-bond range
    ("B", "O", 1.7),   # BO3, BO4
    ("Si", "O", 2.0),  # SiO4
    ("As", "O", 2.0),  # AsO4
    ("V", "O", 2.2),   # VO4
    ("W", "O", 2.2),   # WO4
    ("Mo", "O", 2.2),  # MoO4
    ("Cr", "O", 2.1),  # CrO4
    ("Mn", "O", 2.2),  # MnO4
    ("Re", "O", 2.2),  # ReO4
    # Halide perovskites: metal-halide octahedra.
    ("Pb", "Cl", 3.5), ("Pb", "Br", 3.7), ("Pb", "I", 3.9), ("Pb", "F", 3.0),
    ("Sn", "Cl", 3.3), ("Sn", "Br", 3.5), ("Sn", "I", 3.7),
    ("Bi", "Cl", 3.5), ("Bi", "Br", 3.7), ("Bi", "I", 3.9),
    ("Ge", "Cl", 3.0), ("Ge", "Br", 3.2), ("Ge", "I", 3.4),
    ("Sb", "Cl", 3.3), ("Sb", "Br", 3.5), ("Sb", "I", 3.7),
    # Oxide perovskites and related.
    ("Ti", "O", 2.5), ("Zr", "O", 2.7), ("Hf", "O", 2.6),
    ("Nb", "O", 2.5), ("Ta", "O", 2.5),
    ("Fe", "O", 2.5), ("Co", "O", 2.5), ("Ni", "O", 2.4),
    ("Cu", "O", 2.5), ("Zn", "O", 2.4),
    ("Mg", "O", 2.5), ("Ca", "O", 2.8),
    # Halide-bridged transition metal complexes.
    ("Fe", "Cl", 2.8), ("Fe", "Br", 3.0),
    ("Cu", "Cl", 2.8), ("Cu", "Br", 3.0),
    ("Co", "Cl", 2.8), ("Co", "Br", 3.0),
    ("Mn", "Cl", 2.8), ("Mn", "Br", 3.0),
    ("Ni", "Cl", 2.8), ("Ni", "Br", 3.0),
)


def atom_centered_polyhedra(
    bundle,
    *,
    central: str,
    ligand: str,
    search_cutoff: float | None = None,
) -> list[dict[str, Any]]:
    """Run MolCrysKit's :func:`find_polyhedra` on the bundle's
    underlying ``MolecularCrystal``. Returns per-central-atom dicts
    with PBC-resolved Cartesian shell coordinates -- the same shape
    the renderer already consumes from
    :func:`extract_coordination_shell`, minus the fragment-graph
    cruft.

    Parameters
    ----------
    bundle
        ``LoadedCrystal`` carrying ``crystal`` (the
        ``MolecularCrystal`` MolCrysKit returns) -- normally just
        ``backend.get_bundle(name)``.
    central, ligand
        Element symbols (e.g. ``"Cl"``, ``"O"``) of the central atom
        and ligand atom species. Pass the list ligand if you want
        union-of-elements semantics later; for now a single element
        is enough to cover ClO4 / SO4 / PbCl6 / ...
    search_cutoff
        Optional A--B distance cap forwarded to MolCrysKit. Defaults
        to its own conservative value (large enough for halide-O at
        ~3 A and Pb-Cl at ~3 A but well below cation-cation ranges).
    """
    crystal = getattr(bundle, "crystal", None)
    if crystal is None:
        return []
    central = str(central or "").strip()
    ligand = str(ligand or "").strip()
    if not central or not ligand:
        return []
    try:
        results = find_polyhedra(
            crystal,
            central=central,
            ligand=ligand,
            search_cutoff=search_cutoff,
            score_shape=False,
        )
    except Exception:
        return []
    overlays: list[dict[str, Any]] = []
    for entry in results:
        shell_coords = entry.get("shell_coords")
        center_position = entry.get("center_position")
        distances = entry.get("shell_distances")
        if shell_coords is None or center_position is None:
            continue
        shell_coords_arr = np.asarray(shell_coords, dtype=float)
        center_position_arr = np.asarray(center_position, dtype=float)
        if shell_coords_arr.size == 0 or shell_coords_arr.shape[0] < 3:
            # Need at least 3 vertices for any useful polygon; the
            # renderer's ConvexHull would otherwise reject the shell.
            # CN<3 just means the central atom has no real polyhedron
            # in this structure (e.g. terminal Cl in a chloride salt).
            continue
        center_index = int(entry.get("center_index", -1))
        # Plain Python lists, not numpy arrays: the renderer treats
        # ``shell_coords`` with bare ``if not shell:`` truth checks
        # (which are ambiguous on a 2-D array). Same convention as the
        # fragment-graph path; downstream code rebuilds the array on
        # demand.
        overlays.append(
            {
                "center_coords": [float(v) for v in center_position_arr],
                "center_label": f"{central}{center_index}",
                "shell_coords": shell_coords_arr.tolist(),
                "distances": [float(d) for d in (distances or [])],
                "is_analysis_anchor": False,
                "visible": True,
                "_atom_central": central,
                "_atom_ligand": ligand,
                "_atom_center_index": center_index,
                "coordination_number": int(entry.get("coordination_number", shell_coords_arr.shape[0])),
            }
        )
    return overlays


def suggest_default_polyhedron_specs(
    bundle,
    *,
    palette: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Return a chemistry-sensible default ``polyhedron_specs`` list
    for ``bundle`` by checking which (central, ligand) element pairs
    in :data:`_ATOM_POLY_DEFAULT_CHEMISTRY` actually produce a
    polyhedron in this structure (CN >= 3 around at least one centre).

    The result is a list of atom-centered specs ready to drop into
    ``state['polyhedron_specs']``. Empty list when nothing chemistry-
    obvious is present (a pure organic crystal, for instance).
    """
    crystal = getattr(bundle, "crystal", None)
    if crystal is None:
        return []
    try:
        symbols = set(crystal.to_ase().get_chemical_symbols())
    except Exception:
        symbols = set()
    if not symbols:
        return []
    suggestions: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for central, ligand, max_bond in _ATOM_POLY_DEFAULT_CHEMISTRY:
        if central not in symbols or ligand not in symbols:
            continue
        if (central, ligand) in seen_pairs:
            continue
        # Only suggest the pair when MolCrysKit finds at least one
        # polyhedron WITHIN THE COVALENT-BOND CAP. Without the cap,
        # organic-cation N atoms in perchlorate hybrids would get
        # surrounded by far-away O atoms (3-5 A H-bond contacts) and
        # produce huge cuboctahedra that have no chemistry meaning.
        try:
            sample = atom_centered_polyhedra(
                bundle,
                central=central,
                ligand=ligand,
                search_cutoff=max_bond,
            )
        except Exception:
            sample = []
        if not sample:
            continue
        seen_pairs.add((central, ligand))
        suggestions.append(
            {
                "kind": "atom",
                "name": f"{central}-{ligand}",
                "center_species": central,
                "ligand_species": ligand,
                "search_cutoff": float(max_bond),
                "enabled": True,
                "instance_overrides": {},
            }
        )
    return suggestions


def classify_fragments(bundle) -> list[dict[str, Any]]:
    return list(getattr(bundle, "topology_fragment_table", None) or bundle.fragment_table)


def _lattice_vectors(bundle) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    M = np.array(bundle.M if getattr(bundle, "M", None) is not None else bundle.scene["M"], dtype=float)
    return M[:, 0], M[:, 1], M[:, 2]


def _neighbor_types(fragments: list[dict[str, Any]], center_type: str) -> list[str]:
    """Pick which fragment types should populate the neighbour pool.

    XYn perovskite-style chemistry: cations (A or B) are coordinated by
    anions (X), and X is coordinated by cations. We treat A and B as a
    *single class* of cation when X is the centre; otherwise the classifier's
    A/B size split would arbitrarily exclude half of the surrounding cage
    just because half the cations happen to be heavier than the others.
    """
    available = {frag.get("type", "?") for frag in fragments}
    if center_type in ("A", "B") and "X" in available:
        return ["X"]
    if center_type == "X":
        cations = [t for t in ("A", "B") if t in available]
        if cations:
            return cations
    return [frag_type for frag_type in ("B", "A", "X", "?") if frag_type in available and frag_type != center_type]


def _normalize_search_supercell(value) -> tuple[int, int, int]:
    """Coerce caller input into a (na, nb, nc) span triple.

    ``None`` and falsy values map to ``(0, 0, 0)`` (use cutoff-driven span
    only). Negative numbers clamp to zero -- a polyhedron centre cannot
    request fewer than zero adjacent images.
    """
    if value is None:
        return (0, 0, 0)
    if isinstance(value, (int, float)):
        v = max(0, int(value))
        return (v, v, v)
    seq = tuple(value)
    if len(seq) == 1:
        v = max(0, int(seq[0]))
        return (v, v, v)
    if len(seq) >= 3:
        return (
            max(0, int(seq[0])),
            max(0, int(seq[1])),
            max(0, int(seq[2])),
        )
    raise ValueError(f"search_supercell must be int or 3-tuple, got: {value!r}")


def _translation_grid(
    bundle,
    cutoff: float,
    *,
    search_supercell: tuple[int, int, int] = (0, 0, 0),
) -> list[tuple[int, int, int, np.ndarray]]:
    lattice = _lattice_vectors(bundle)
    ranges = []
    for vec, extra in zip(lattice, search_supercell):
        length = max(np.linalg.norm(vec), 1e-6)
        cutoff_span = max(1, int(math.ceil((cutoff + 1.0) / length)))
        # ``search_supercell`` is a *floor* on the search radius (in lattice
        # units). Cutoff still wins when it requests more images than the
        # caller asked for.
        span = max(cutoff_span, int(extra))
        ranges.append(range(-span, span + 1))
    translations = []
    for na, nb, nc in itertools.product(*ranges):
        shift_vec = na * lattice[0] + nb * lattice[1] + nc * lattice[2]
        translations.append((na, nb, nc, shift_vec))
    return translations


def _neighbor_pool_uncached(
    bundle,
    center_fragment: dict,
    cutoff: float,
    *,
    ligand_species: tuple[str, ...] | None = None,
    search_supercell: tuple[int, int, int] = (0, 0, 0),
) -> list[dict[str, Any]]:
    """Find neighbour fragments within ``cutoff`` of the centre.

    ``ligand_species`` overrides the default perovskite XYn neighbour-type
    inference: when set, only fragments whose ``formula``/``species``
    matches one of the listed strings are considered. ``None`` keeps the
    legacy auto-derived behaviour (``_neighbor_types``).

    Pre-built named ``polyhedron_specs`` need this override so the user
    can paint e.g. C8N1 -> Cl polyhedra in DAP-4 explicitly without
    fighting the A/B/X auto-classifier.

    ``search_supercell`` is a per-axis *floor* on the lattice-image
    search radius. Cutoff still drives the natural span; the floor only
    matters when the caller wants polyhedra to extend across cell
    boundaries even when cutoff alone would not have searched that far.
    """
    fragments = classify_fragments(bundle)
    center_type = center_fragment.get("type", "?")
    if ligand_species:
        wanted = {str(item) for item in ligand_species if item}
        allowed_types: set[str] = set()
    else:
        wanted = None
        allowed_types = set(_neighbor_types(fragments, center_type))
    center = np.array(center_fragment["center"], dtype=float)
    translations = _translation_grid(bundle, cutoff, search_supercell=search_supercell)
    fragment_entries = []
    for fragment_order, fragment in enumerate(fragments):
        if fragment["index"] == center_fragment["index"] and center_type not in {"X"}:
            continue
        if wanted is not None:
            formula_key = fragment.get("formula") or fragment.get("species")
            if formula_key not in wanted:
                continue
        elif allowed_types and fragment.get("type", "?") not in allowed_types:
            continue
        fragment_entries.append((fragment_order, fragment))
    if not fragment_entries or not translations:
        return []

    base_centers = np.array([frag["center"] for _, frag in fragment_entries], dtype=float)
    shift_vectors = np.array([item[3] for item in translations], dtype=float)
    distances = np.linalg.norm(base_centers[:, None, :] + shift_vectors[None, :, :] - center, axis=-1)
    mask = (distances > 1e-8) & (distances <= float(cutoff))

    center_idx = int(center_fragment["index"])
    zero_translation = np.array(
        [(na, nb, nc) == (0, 0, 0) for na, nb, nc, _ in translations],
        dtype=bool,
    )
    for row, (_, fragment) in enumerate(fragment_entries):
        if int(fragment["index"]) == center_idx:
            mask[row, zero_translation] = False

    rows, cols = np.nonzero(mask)
    if len(rows) == 0:
        return []

    insertion_order = np.array(
        [fragment_entries[row][0] * len(translations) + int(col) for row, col in zip(rows, cols)],
        dtype=int,
    )
    ranked = np.lexsort((insertion_order, distances[rows, cols]))
    candidates = []
    for pos in ranked:
        row = int(rows[pos])
        col = int(cols[pos])
        fragment = fragment_entries[row][1]
        na, nb, nc, shift_vec = translations[col]
        point = base_centers[row] + shift_vec
        item = dict(fragment)
        item["image_shift"] = [na, nb, nc]
        item["center"] = [float(x) for x in point]
        item["distance"] = float(distances[row, col])
        candidates.append(item)
    return candidates


def _neighbor_pool(
    bundle,
    center_fragment: dict,
    cutoff: float,
    *,
    ligand_species: tuple[str, ...] | None = None,
    search_supercell: tuple[int, int, int] = (0, 0, 0),
) -> list[dict[str, Any]]:
    """Cached PBC neighbour search. The cache key now includes the
    ligand-species filter and the search-supercell floor so two specs
    with the same centre but different restrictions don't poison each
    other's pool."""
    cache = getattr(bundle, "_neighbor_pool_cache", None)
    if cache is None:
        cache = {}
        try:
            bundle._neighbor_pool_cache = cache
        except Exception:
            return _neighbor_pool_uncached(
                bundle,
                center_fragment,
                cutoff,
                ligand_species=ligand_species,
                search_supercell=search_supercell,
            )
    ligand_key = tuple(sorted(ligand_species)) if ligand_species else None
    super_key = tuple(int(v) for v in search_supercell)
    key = (
        int(center_fragment.get("index", -1)),
        float(cutoff),
        ligand_key,
        super_key,
    )
    if key not in cache:
        cache[key] = _neighbor_pool_uncached(
            bundle,
            center_fragment,
            cutoff,
            ligand_species=ligand_species,
            search_supercell=search_supercell,
        )
    return cache[key]


def _extract_coordination_shell_static(
    bundle,
    center_index: int,
    cutoff: float,
    *,
    ligand_species: tuple[str, ...] | None = None,
    search_supercell: tuple[int, int, int] = (0, 0, 0),
) -> dict[str, Any]:
    """Run the geometric part of ``extract_coordination_shell`` -- everything
    that depends only on (bundle, center_index, cutoff, ligand_species,
    search_supercell) and not on the per-call display-coordinate offsets.
    The result is cacheable; the public wrapper layers display fields on
    top of a shallow copy."""
    fragments = classify_fragments(bundle)
    center_fragment = next((frag for frag in fragments if int(frag["index"]) == int(center_index)), None)
    if center_fragment is None:
        raise IndexError(f"Unknown fragment index: {center_index}")
    source_center = np.array(center_fragment["center"], dtype=float)
    candidates = _neighbor_pool(
        bundle,
        center_fragment,
        cutoff=cutoff,
        ligand_species=ligand_species,
        search_supercell=search_supercell,
    )
    candidate_coords = (
        np.array([item["center"] for item in candidates], dtype=float)
        if candidates else np.zeros((0, 3), dtype=float)
    )
    cn_info = detect_coordination_number(
        [item["distance"] for item in candidates],
        coords=candidate_coords,
        center=source_center,
        enforce_enclosure=True,
    )
    cn = int(cn_info["coordination_number"])
    shell = candidates[:cn]
    source_shell_coords = (
        np.array([item["center"] for item in shell], dtype=float)
        if shell else np.zeros((0, 3), dtype=float)
    )
    shell_distances = [float(item["distance"]) for item in shell]
    return {
        "center_index": int(center_index),
        "default_label": center_fragment.get("label", f"site-{center_index}"),
        "default_type": center_fragment.get("type", "?"),
        "center_formula": center_fragment.get("formula") or center_fragment.get("species"),
        "source_center_coords": source_center,
        "cutoff": float(cutoff),
        "neighbor_pool_size": len(candidates),
        "coordination_number": cn,
        "gap_info": cn_info,
        "shell": shell,
        "candidate_fragments": candidates,
        "source_shell_coords": source_shell_coords,
        "distances": shell_distances,
        "all_distances": [float(item["distance"]) for item in candidates],
    }


def _cached_extract_static(
    bundle,
    center_index: int,
    cutoff: float,
    *,
    ligand_species: tuple[str, ...] | None = None,
    search_supercell: tuple[int, int, int] = (0, 0, 0),
) -> dict[str, Any]:
    cache = getattr(bundle, "_shell_cache", None)
    if cache is None:
        cache = {}
        try:
            bundle._shell_cache = cache
        except Exception:
            return _extract_coordination_shell_static(
                bundle,
                center_index,
                cutoff,
                ligand_species=ligand_species,
                search_supercell=search_supercell,
            )
    ligand_key = tuple(sorted(ligand_species)) if ligand_species else None
    super_key = tuple(int(v) for v in search_supercell)
    key = (int(center_index), float(cutoff), ligand_key, super_key)
    if key not in cache:
        cache[key] = _extract_coordination_shell_static(
            bundle,
            center_index,
            cutoff,
            ligand_species=ligand_species,
            search_supercell=search_supercell,
        )
    return cache[key]


def extract_coordination_shell(
    bundle,
    center_index: int,
    cutoff: float = 10.0,
    *,
    display_center: Iterable[float] | None = None,
    display_label: str | None = None,
    display_type: str | None = None,
    ligand_species: Iterable[str] | None = None,
    search_supercell=None,
) -> dict[str, Any]:
    ligand_tuple = tuple(str(item) for item in ligand_species) if ligand_species else None
    super_tuple = _normalize_search_supercell(search_supercell)
    static = _cached_extract_static(
        bundle,
        int(center_index),
        float(cutoff),
        ligand_species=ligand_tuple,
        search_supercell=super_tuple,
    )
    source_center = np.asarray(static["source_center_coords"], dtype=float)
    plot_center = source_center if display_center is None else np.array(display_center, dtype=float)
    delta = plot_center - source_center

    source_shell_coords = np.asarray(static["source_shell_coords"], dtype=float)
    shell_coords = (
        source_shell_coords + delta if len(source_shell_coords) else np.zeros((0, 3), dtype=float)
    )
    candidates = static["candidate_fragments"]
    pool_coords_arr = (
        np.array([item["center"] for item in candidates], dtype=float) + delta
        if candidates else np.zeros((0, 3), dtype=float)
    )
    return {
        "center_index": int(center_index),
        "center_label": display_label or static["default_label"],
        "center_type": display_type or static["default_type"],
        "center_formula": static["center_formula"],
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
    search_supercell: tuple[int, int, int] = (0, 0, 0),
) -> dict[str, Any]:
    shell = extract_coordination_shell(
        bundle,
        center_index=center_index,
        cutoff=cutoff,
        display_center=display_center,
        display_label=display_label,
        display_type=display_type,
        ligand_species=ligand_species,
        search_supercell=search_supercell,
    )
    center = shell["center_coords"]
    shell_coords = shell["shell_coords"]
    angular = angular_rmsd_vs_ideals(shell_coords, center=center)
    planarity = planarity_analysis(shell_coords, group_size=min(5, len(shell_coords)) if shell_coords else 5)
    prism = detect_prism_vs_antiprism(shell_coords)
    hull = convex_hull_payload(shell_coords)
    return {
        **shell,
        "angular": angular,
        "planarity": planarity,
        "prism_analysis": prism,
        "hull": hull,
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
    search_supercell=None,
) -> dict[str, Any]:
    """Cached primary-site analysis. The heavy ``planarity_analysis`` pass
    runs ``itertools.combinations`` of size 5 over the shell, which gets
    expensive for CN=12 / large neighbour pools. We key the cache on
    ``(center_index, cutoff, ligand_species, search_supercell)`` -- the
    full bundle topology is immutable once loaded -- so flipping species
    checkboxes back and forth no longer redoes the work, but two named
    polyhedron specs with different ligand restrictions or search ranges
    get distinct cache slots.

    ``search_supercell`` is a per-axis floor on the lattice-image search
    range. It is decoupled from the *display* supercell (a structural
    transform): callers can keep a single cell on screen but ask for
    polyhedra to wrap to neighbouring images, or repeat the structure
    without inflating the search radius. Accepts ``int``, ``(na, nb, nc)``
    triples, or ``None`` (cutoff-driven span only).
    """
    ligand_tuple = tuple(str(item) for item in ligand_species) if ligand_species else None
    super_tuple = _normalize_search_supercell(search_supercell)
    cache = getattr(bundle, "_analyze_topology_cache", None)
    if cache is None:
        cache = {}
        try:
            bundle._analyze_topology_cache = cache
        except Exception:
            return _analyze_topology_uncached(
                bundle, center_index, cutoff,
                display_center, display_label, display_type,
                ligand_species=ligand_tuple,
                search_supercell=super_tuple,
            )
    key = (int(center_index), float(cutoff), ligand_tuple, super_tuple)
    cached = cache.get(key)
    if cached is None:
        cached = _analyze_topology_uncached(
            bundle, center_index, cutoff,
            None, None, None,  # cache on the static result; overlay display fields below
            ligand_species=ligand_tuple,
            search_supercell=super_tuple,
        )
        cache[key] = cached
    # Display fields shift per call (camera / formula-unit centering); patch
    # them onto a shallow copy so the cache stays generic.
    out = dict(cached)
    if display_center is not None:
        plot_center = np.array(display_center, dtype=float)
        source_center = np.array(out.get("source_center_coords", plot_center), dtype=float)
        delta = plot_center - source_center
        out["center_coords"] = plot_center.tolist()
        if out.get("source_shell_coords"):
            shell = np.array(out["source_shell_coords"], dtype=float) + delta
            out["shell_coords"] = shell.tolist()
    if display_label is not None:
        out["center_label"] = display_label
    if display_type is not None:
        out["center_type"] = display_type
    return out
