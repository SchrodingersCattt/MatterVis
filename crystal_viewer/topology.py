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
    hull_encloses_center as _hull_encloses_center,
    planarity_analysis,
)
from molcrys_kit.structures.polyhedra import convex_hull_payload, ideal_polyhedra_for_cn

__all__ = [
    "DEFAULT_CENTROID_OFFSET_FRAC",
    "_hull_encloses_center",
    "analyze_topology",
    "angular_rmsd_vs_ideals",
    "classify_fragments",
    "compute_angular_signature",
    "convex_hull_payload",
    "detect_coordination_number",
    "detect_prism_vs_antiprism",
    "extract_coordination_shell",
    "ideal_polyhedra_for_cn",
    "planarity_analysis",
]


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


def _translation_grid(bundle, cutoff: float) -> list[tuple[int, int, int, np.ndarray]]:
    lattice = _lattice_vectors(bundle)
    ranges = []
    for vec in lattice:
        length = max(np.linalg.norm(vec), 1e-6)
        span = max(1, int(math.ceil((cutoff + 1.0) / length)))
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
) -> list[dict[str, Any]]:
    """Find neighbour fragments within ``cutoff`` of the centre.

    ``ligand_species`` overrides the default perovskite XYn neighbour-type
    inference: when set, only fragments whose ``formula``/``species``
    matches one of the listed strings are considered. ``None`` keeps the
    legacy auto-derived behaviour (``_neighbor_types``).

    Pre-built named ``polyhedron_specs`` need this override so the user
    can paint e.g. C8N1 -> Cl polyhedra in DAP-4 explicitly without
    fighting the A/B/X auto-classifier.
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
    translations = _translation_grid(bundle, cutoff)
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
) -> list[dict[str, Any]]:
    """Cached PBC neighbour search. The cache key now includes the
    ligand-species filter so two specs with the same centre but
    different ligand restrictions don't poison each other's pool."""
    cache = getattr(bundle, "_neighbor_pool_cache", None)
    if cache is None:
        cache = {}
        try:
            bundle._neighbor_pool_cache = cache
        except Exception:
            return _neighbor_pool_uncached(
                bundle, center_fragment, cutoff, ligand_species=ligand_species
            )
    ligand_key = tuple(sorted(ligand_species)) if ligand_species else None
    key = (int(center_fragment.get("index", -1)), float(cutoff), ligand_key)
    if key not in cache:
        cache[key] = _neighbor_pool_uncached(
            bundle, center_fragment, cutoff, ligand_species=ligand_species
        )
    return cache[key]


def _extract_coordination_shell_static(
    bundle,
    center_index: int,
    cutoff: float,
    *,
    ligand_species: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run the geometric part of ``extract_coordination_shell`` -- everything
    that depends only on (bundle, center_index, cutoff, ligand_species) and
    not on the per-call display-coordinate offsets. The result is
    cacheable; the public wrapper layers display fields on top of a
    shallow copy."""
    fragments = classify_fragments(bundle)
    center_fragment = next((frag for frag in fragments if int(frag["index"]) == int(center_index)), None)
    if center_fragment is None:
        raise IndexError(f"Unknown fragment index: {center_index}")
    source_center = np.array(center_fragment["center"], dtype=float)
    candidates = _neighbor_pool(
        bundle, center_fragment, cutoff=cutoff, ligand_species=ligand_species
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
) -> dict[str, Any]:
    cache = getattr(bundle, "_shell_cache", None)
    if cache is None:
        cache = {}
        try:
            bundle._shell_cache = cache
        except Exception:
            return _extract_coordination_shell_static(
                bundle, center_index, cutoff, ligand_species=ligand_species
            )
    ligand_key = tuple(sorted(ligand_species)) if ligand_species else None
    key = (int(center_index), float(cutoff), ligand_key)
    if key not in cache:
        cache[key] = _extract_coordination_shell_static(
            bundle, center_index, cutoff, ligand_species=ligand_species
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
) -> dict[str, Any]:
    ligand_tuple = tuple(str(item) for item in ligand_species) if ligand_species else None
    static = _cached_extract_static(
        bundle, int(center_index), float(cutoff), ligand_species=ligand_tuple
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
) -> dict[str, Any]:
    shell = extract_coordination_shell(
        bundle,
        center_index=center_index,
        cutoff=cutoff,
        display_center=display_center,
        display_label=display_label,
        display_type=display_type,
        ligand_species=ligand_species,
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
) -> dict[str, Any]:
    """Cached primary-site analysis. The heavy ``planarity_analysis`` pass
    runs ``itertools.combinations`` of size 5 over the shell, which gets
    expensive for CN=12 / large neighbour pools. We key the cache on
    ``(center_index, cutoff, ligand_species)`` -- the full bundle topology
    is immutable once loaded -- so flipping species checkboxes back and
    forth no longer redoes the work, but two named polyhedron specs with
    different ligand restrictions get distinct cache slots."""
    ligand_tuple = tuple(str(item) for item in ligand_species) if ligand_species else None
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
            )
    key = (int(center_index), float(cutoff), ligand_tuple)
    cached = cache.get(key)
    if cached is None:
        cached = _analyze_topology_uncached(
            bundle, center_index, cutoff,
            None, None, None,  # cache on the static result; overlay display fields below
            ligand_species=ligand_tuple,
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
