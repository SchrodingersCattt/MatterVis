from __future__ import annotations
# ruff: noqa: F401,F403,F405

from collections import OrderedDict

from .shared import *
from .normalizers import *
from .rightclick import _normalize_polyhedron_specs
from ..topology import (
    atom_overlay as _atom_overlay,
    display_atom_centers_for_spec as _display_atom_centers_for_spec,
    extract_atom_coordination_shells,
)
from ..topology.analysis import EXPLICIT_LIGAND_MESSAGE, explicit_ligand_species
from ..cache_keys import topology_geometry_cache_key
from ..render.picking import polyhedron_instance_id


_TOPOLOGY_CACHE_LIMIT = 8
_DISORDER_CENTER_DEDUPE_TOL = 0.75



def _pbc_distance_for_bundle(bundle: LoadedCrystal, frac_a, frac_b) -> float:
    return float(
        minimum_image_distance(
            np.array(frac_b, dtype=float),
            np.array(frac_a, dtype=float),
            np.array(bundle.M, dtype=float),
        )
    )


def map_display_fragment_to_topology_for_bundle(
    bundle: LoadedCrystal,
    display_fragment: dict | None,
) -> Optional[dict[str, Any]]:
    if display_fragment is None:
        return None
    source_molecule_index = display_fragment.get("source_molecule_index")
    if source_molecule_index is not None:
        matched = next(
            (
                fragment
                for fragment in bundle.topology_fragment_table
                if fragment.get("source_molecule_index") == source_molecule_index
            ),
            None,
        )
        if matched is not None:
            return matched
    display_formula = display_fragment.get("formula") or display_fragment.get("species")
    candidates = [
        fragment
        for fragment in bundle.topology_fragment_table
        if (fragment.get("formula") or fragment.get("species")) == display_formula
    ]
    if not candidates:
        candidates = [
            fragment
            for fragment in bundle.topology_fragment_table
            if fragment.get("type") == display_fragment.get("type")
        ]
    if not candidates:
        candidates = list(bundle.topology_fragment_table)
    if not candidates:
        return None
    display_frac = np.array(display_fragment.get("frac_center", [0.0, 0.0, 0.0]), dtype=float)
    ranked = []
    for fragment in candidates:
        ranked.append(
            (
                _pbc_distance_for_bundle(
                    bundle,
                    display_frac,
                    fragment.get("frac_center", [0.0, 0.0, 0.0]),
                ),
                fragment,
            )
        )
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def compute_topology_geometry_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    return compute_topology_geometry(
        bundle=payload["bundle"],
        scene=payload["scene"],
        effective_specs=payload["effective_specs"],
        site_index=int(payload["site_index"]),
        cutoff=float(payload["cutoff"]),
    )


def _fragment_minor_score(scene: dict[str, Any], fragment: dict[str, Any]) -> tuple[bool, int, int]:
    atoms = scene.get("draw_atoms") or []
    flags = []
    for site_idx in fragment.get("site_indices") or []:
        try:
            atom = atoms[int(site_idx)]
        except (TypeError, ValueError, IndexError):
            continue
        flags.append(bool(atom.get("is_minor", atom.get("_is_minor", False))))
    minor_count = sum(1 for flag in flags if flag)
    return (
        bool(flags) and minor_count == len(flags),
        minor_count,
        int(fragment.get("index", 0) or 0),
    )


def _dedupe_disorder_center_fragments(
    bundle,
    scene: dict[str, Any],
    fragments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse PART/orientation alternatives that share a molecular centre.

    Disorder alternatives can appear as separate display fragments with
    near-identical centres. Rendering all of them tiles overlapping
    polyhedra. Keep the best representative, preferring the major
    orientation, while leaving genuinely distinct molecules alone.
    """
    representatives: list[dict[str, Any]] = []
    for fragment in fragments:
        formula = fragment.get("formula") or fragment.get("species")
        duplicate_at: int | None = None
        for index, representative in enumerate(representatives):
            rep_formula = representative.get("formula") or representative.get("species")
            if rep_formula != formula:
                continue
            distance = float(
                np.linalg.norm(
                    np.asarray(fragment.get("center", [0.0, 0.0, 0.0]), dtype=float)
                    - np.asarray(representative.get("center", [0.0, 0.0, 0.0]), dtype=float)
                )
            )
            if distance <= _DISORDER_CENTER_DEDUPE_TOL:
                duplicate_at = index
                break
        if duplicate_at is None:
            representatives.append(fragment)
            continue
        current = representatives[duplicate_at]
        if _fragment_minor_score(scene, fragment) < _fragment_minor_score(scene, current):
            representatives[duplicate_at] = fragment
    return representatives


def _empty_primary(display_fragment: dict[str, Any] | None) -> dict[str, Any]:
    """Analysis payload used when no explicit ligand is available.

    The anchor spec can still be drawn once a ligand is chosen. Until then
    the geometry path must not call MolCrysKit with a missing ligand.
    """
    center = [0.0, 0.0, 0.0]
    if display_fragment and display_fragment.get("center") is not None:
        center = [float(value) for value in display_fragment["center"]]
    return {
        "center_coords": list(center),
        "center_label": display_fragment.get("label") if display_fragment else None,
        "center_type": display_fragment.get("type") if display_fragment else None,
        "shell_coords": [],
        "distances": [],
        "hull": None,
        "source_center_coords": list(center),
        "source_shell_coords": [],
        "source_hull": None,
        "shape": {},
        "coordination_number": 0,
        "shell": [],
    }


def _overlay_drawable_hull_size(overlay: dict[str, Any]) -> int:
    shell = overlay.get("shell_coords") or []
    hull = overlay.get("hull") or {}
    simplices = hull.get("simplices") or []
    return len(shell) if len(shell) >= 4 and len(simplices) > 0 else 0


def _fragment_matches_polyhedron_spec(fragment: dict[str, Any], spec: dict[str, Any]) -> bool:
    center_species = str(spec.get("center_species") or "")
    if str(spec.get("level") or "molecule") == "atom":
        return center_species in {str(elem) for elem in (fragment.get("elem_set") or [])}
    return (fragment.get("formula") or fragment.get("species")) == center_species


def _molecule_center_image_shift(
    display_fragment: dict[str, Any],
    source_fragment: dict[str, Any],
) -> list[int]:
    """Return the stable complete-cell image of a displayed molecule.

    Molecule-level topology overlays are translated from a source fragment to
    each displayed fragment.  ``fragment_table.image_shift`` is relative to
    MolCrysKit's unwrapped molecule and therefore is not a stable identity
    token.  Prefer the absolute fractional-centre difference (the same
    convention used by the agent topology path), and retain the legacy field
    as a fallback for lightweight test/third-party bundles.
    """

    display_frac = np.asarray(display_fragment.get("frac_center"), dtype=float)
    source_frac = np.asarray(source_fragment.get("frac_center"), dtype=float)
    if display_frac.shape == (3,) and source_frac.shape == (3,):
        source_frac = source_frac - np.floor(source_frac)
        source_frac[np.isclose(source_frac, 0.0, rtol=0.0, atol=1e-6)] = 0.0
        source_frac[np.isclose(source_frac, 1.0, rtol=0.0, atol=1e-6)] = 0.0
        delta = display_frac - source_frac
        image = np.rint(delta).astype(int)
        if np.allclose(delta, image, rtol=0.0, atol=1e-6):
            return [int(value) for value in image]
    raw_shift = display_fragment.get("image_shift")
    if isinstance(raw_shift, (list, tuple)) and len(raw_shift) == 3:
        try:
            return [int(value) for value in raw_shift]
        except (TypeError, ValueError):
            pass
    return [0, 0, 0]


def _molecule_overlay_identity(
    display_fragment: dict[str, Any],
    source_fragment: dict[str, Any],
) -> dict[str, Any]:
    """Build identity metadata shared by mesh faces and pick markers."""

    # ``center_source_index`` names the canonical topology-fragment index,
    # matching the agent topology payload.  ``source_molecule_index`` is a
    # MolCrysKit bridge key and may differ after MatterVis sorts fragments.
    source_index = source_fragment.get("index")
    if source_index is None:
        source_index = source_fragment.get("source_molecule_index")
    if source_index is None:
        source_index = display_fragment.get("source_molecule_index")
    try:
        source_index = int(source_index)
    except (TypeError, ValueError):
        source_index = None
    display_index = display_fragment.get("index")
    try:
        display_index = int(display_index)
    except (TypeError, ValueError):
        display_index = None
    image_shift = _molecule_center_image_shift(display_fragment, source_fragment)
    identity = {
        "center_source_index": source_index,
        "center_display_index": display_index,
        "center_image": list(image_shift),
        "center_image_shift": list(image_shift),
    }
    return identity


def _stamp_molecule_overlay_identity(
    overlay: dict[str, Any],
    *,
    spec_id: str,
    display_fragment: dict[str, Any],
    source_fragment: dict[str, Any],
    ordinal: int,
) -> dict[str, Any]:
    """Stamp a deterministic ID and source metadata onto one overlay."""

    overlay.update(_molecule_overlay_identity(display_fragment, source_fragment))
    overlay["instance_id"] = polyhedron_instance_id(spec_id, overlay, ordinal)
    return overlay


def compute_topology_geometry(
    *,
    bundle,
    scene: dict[str, Any],
    effective_specs: list[dict[str, Any]],
    site_index: int,
    cutoff: float,
) -> Optional[dict[str, Any]]:
    display_fragment = next(
        (fragment for fragment in scene.get("fragment_table", []) if int(fragment["index"]) == int(site_index)),
        None,
    )
    topology_fragment = map_display_fragment_to_topology_for_bundle(bundle, display_fragment)
    if topology_fragment is None:
        return None

    center_to_spec_indices: dict[str, list[int]] = {}
    for index, spec in enumerate(effective_specs):
        center_to_spec_indices.setdefault(spec["center_species"], []).append(index)

    primary_display_index = int(display_fragment["index"]) if display_fragment else None
    analysis_spec_index = 0
    if display_fragment:
        analysis_spec_index = next(
            (
                index
                for index, spec in enumerate(effective_specs)
                if _fragment_matches_polyhedron_spec(display_fragment, spec)
            ),
            0,
        )
    analysis_spec = effective_specs[analysis_spec_index]
    analysis_ligand = explicit_ligand_species(analysis_spec.get("ligand_species"))
    analysis_enforce_enclosure = bool(analysis_spec.get("enforce_enclosure", True))
    analysis_centroid_offset_frac = float(
        analysis_spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)
    )
    analysis_level = str(analysis_spec.get("level") or "molecule")
    analysis_center_kind = str(analysis_spec.get("center_kind") or "centroid")
    analysis_hard_cutoff = analysis_spec.get("hard_cutoff")
    analysis_fallback_max = analysis_spec.get("fallback_max")

    # A null or "(auto)" ligand is persisted state, not a MolCrysKit query.
    # Skipping the call keeps the render worker from surfacing ValueError
    # while the user still has to pick a real ligand.
    if analysis_ligand:
        primary = analyze_topology(
            bundle,
            center_index=int(topology_fragment["index"]),
            cutoff=cutoff,
            display_center=display_fragment.get("center") if display_fragment else None,
            display_label=display_fragment.get("label") if display_fragment else None,
            display_type=display_fragment.get("type") if display_fragment else None,
            ligand_species=[analysis_ligand],
            center_species=analysis_spec.get("center_species"),
            enforce_enclosure=analysis_enforce_enclosure,
            centroid_offset_frac=analysis_centroid_offset_frac,
            level=analysis_level,
            center_kind=analysis_center_kind,
            hard_cutoff=analysis_hard_cutoff,
            fallback_max=analysis_fallback_max,
        )
    else:
        primary = _empty_primary(display_fragment)

    spec_results: list[dict[str, Any]] = []
    legacy_extras: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, spec in enumerate(effective_specs):
        center_species = spec["center_species"]
        ligand = explicit_ligand_species(spec.get("ligand_species"))
        ligand_arg = [ligand] if ligand else None
        enforce_enclosure = bool(spec.get("enforce_enclosure", True))
        centroid_offset_frac = float(spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC))
        spec_level = str(spec.get("level") or "molecule")
        spec_center_kind = str(spec.get("center_kind") or "centroid")
        spec_hard_cutoff = spec.get("hard_cutoff")
        spec_fallback_max = spec.get("fallback_max")
        if ligand is None:
            warnings.append(
                f"{spec.get('name') or center_species}: {EXPLICIT_LIGAND_MESSAGE}"
            )
            spec_results.append(
                {
                    "spec_id": spec["id"],
                    "name": spec["name"],
                    "center_species": center_species,
                    "ligand_species": None,
                    "enforce_enclosure": enforce_enclosure,
                    "centroid_offset_frac": centroid_offset_frac,
                    "level": spec_level,
                    "center_kind": spec_center_kind,
                    "hard_cutoff": spec_hard_cutoff,
                    "fallback_max": spec_fallback_max,
                    "overlays": [],
                }
            )
            continue
        overlays: list[dict[str, Any]] = []
        if spec_level == "atom" and ligand:
            atom_centers = _display_atom_centers_for_spec(bundle, scene, spec)
            shells = extract_atom_coordination_shells(
                bundle,
                cutoff,
                center_species=center_species,
                ligand_species=ligand,
                source_indices={center["source_index"] for center in atom_centers},
                enforce_enclosure=enforce_enclosure,
                centroid_offset_frac=centroid_offset_frac,
                fallback_max=spec_fallback_max,
            )
            overlays = []
            for overlay_ordinal, center in enumerate(atom_centers):
                if center["source_index"] not in shells:
                    continue
                overlay = _atom_overlay(shells[center["source_index"]], center)
                # ``_atom_overlay`` predates semantic polyhedron picking and
                # only carried ``center_image``/``center_draw_index``.  Stamp
                # the canonical aliases so merged meshes and pick markers use
                # the same ID even when style buckets or skipped overlays
                # change their local ordinal.
                overlay["center_image_shift"] = list(center.get("image") or [0, 0, 0])
                overlay["center_display_index"] = int(center.get("draw_index", overlay_ordinal))
                overlay["instance_id"] = polyhedron_instance_id(
                    str(spec["id"]), overlay, overlay_ordinal
                )
                overlays.append(overlay)
            if overlays and index == analysis_spec_index:
                overlays[0]["is_analysis_anchor"] = True
            spec_results.append(
                {
                    "spec_id": spec["id"],
                    "name": spec["name"],
                    "center_species": center_species,
                    "ligand_species": ligand,
                    "enforce_enclosure": enforce_enclosure,
                    "centroid_offset_frac": centroid_offset_frac,
                    "level": spec_level,
                    "center_kind": spec_center_kind,
                    "hard_cutoff": spec_hard_cutoff,
                    "fallback_max": spec_fallback_max,
                    "overlays": overlays,
                }
            )
            if atom_centers and not any(_overlay_drawable_hull_size(item) for item in overlays):
                warnings.append(
                    f"{spec.get('name') or center_species}: no drawable polyhedron for "
                    f"{center_species} -> {ligand}; need at least 4 non-coplanar ligand points."
                )
            continue
        candidate_fragments = [
            frag
            for frag in (scene.get("fragment_table") or [])
            if _fragment_matches_polyhedron_spec(frag, spec)
        ]
        center_fragments = _dedupe_disorder_center_fragments(bundle, scene, candidate_fragments)
        for overlay_ordinal, frag in enumerate(center_fragments):
            is_anchor = (
                index == analysis_spec_index
                and primary_display_index is not None
                and int(frag["index"]) == primary_display_index
            )
            if is_anchor:
                overlay = {
                    "center_coords": primary["center_coords"],
                    "center_label": primary.get("center_label"),
                    "shell_coords": primary["shell_coords"],
                    "distances": primary["distances"],
                    "hull": primary.get("hull"),
                        "source_center_coords": primary.get("source_center_coords"),
                        "source_shell_coords": primary.get("source_shell_coords"),
                        "source_hull": primary.get("source_hull"),
                    "is_analysis_anchor": True,
                }
                _stamp_molecule_overlay_identity(
                    overlay,
                    spec_id=str(spec["id"]),
                    display_fragment=frag,
                    source_fragment=topology_fragment,
                    ordinal=overlay_ordinal,
                )
                overlays.append(overlay)
                continue
            mapped = map_display_fragment_to_topology_for_bundle(bundle, frag)
            if mapped is None:
                continue
            try:
                extra = extract_coordination_shell(
                    bundle,
                    center_index=int(mapped["index"]),
                    cutoff=cutoff,
                    display_center=frag.get("center"),
                    display_label=frag.get("label"),
                    display_type=frag.get("type"),
                    ligand_species=ligand_arg,
                    center_species=center_species,
                    enforce_enclosure=enforce_enclosure,
                    centroid_offset_frac=centroid_offset_frac,
                    level=spec_level,
                    center_kind=spec_center_kind,
                    hard_cutoff=spec_hard_cutoff,
                    fallback_max=spec_fallback_max,
                )
            except Exception:
                continue
            if not extra.get("shell_coords"):
                continue
            overlay = {
                "center_coords": extra.get("center_coords"),
                "center_label": extra.get("center_label"),
                "shell_coords": extra.get("shell_coords"),
                "distances": extra.get("distances"),
                "hull": extra.get("hull"),
                "source_center_coords": extra.get("source_center_coords"),
                "source_shell_coords": extra.get("source_shell_coords"),
                "source_hull": extra.get("source_hull"),
                "is_analysis_anchor": False,
            }
            _stamp_molecule_overlay_identity(
                overlay,
                spec_id=str(spec["id"]),
                display_fragment=frag,
                source_fragment=mapped,
                ordinal=overlay_ordinal,
            )
            overlays.append(overlay)
            legacy_extras.append(
                {
                    "center_coords": overlay["center_coords"],
                    "center_label": overlay["center_label"],
                    "shell_coords": overlay["shell_coords"],
                    "distances": overlay["distances"],
                    "hull": overlay.get("hull"),
                    "source_center_coords": overlay.get("source_center_coords"),
                    "source_shell_coords": overlay.get("source_shell_coords"),
                    "source_hull": overlay.get("source_hull"),
                    "center_source_index": overlay.get("center_source_index"),
                    "center_display_index": overlay.get("center_display_index"),
                    "center_image": overlay.get("center_image"),
                    "center_image_shift": overlay.get("center_image_shift"),
                    "instance_id": overlay.get("instance_id"),
                }
            )
        spec_results.append(
            {
                "spec_id": spec["id"],
                "name": spec["name"],
                "center_species": center_species,
                "ligand_species": ligand,
                "enforce_enclosure": enforce_enclosure,
                "centroid_offset_frac": centroid_offset_frac,
                "level": spec_level,
                "center_kind": spec_center_kind,
                "hard_cutoff": spec_hard_cutoff,
                "fallback_max": spec_fallback_max,
                "overlays": overlays,
            }
        )
        drawable_count = sum(1 for overlay in overlays if _overlay_drawable_hull_size(overlay))
        if center_fragments and drawable_count == 0:
            max_shell = max((len(overlay.get("shell_coords") or []) for overlay in overlays), default=0)
            mode = "Gap + enclosure" if enforce_enclosure else "Gap only"
            warnings.append(
                f"{spec.get('name') or center_species}: no drawable polyhedron for "
                f"{center_species} -> {ligand or '(auto)'} ({mode}); "
                f"largest shell has {max_shell} ligand point(s), need at least 4 non-coplanar points."
            )
        elif len(center_fragments) < len(candidate_fragments):
            warnings.append(
                f"{spec.get('name') or center_species}: collapsed "
                f"{len(candidate_fragments) - len(center_fragments)} overlapping disorder/PART centre(s)."
            )

    primary = dict(primary)
    if legacy_extras:
        primary["extra_overlays"] = legacy_extras
    primary["spec_results"] = spec_results
    if warnings:
        primary["warnings"] = warnings
    primary["analysis_spec_id"] = analysis_spec["id"]
    return primary
