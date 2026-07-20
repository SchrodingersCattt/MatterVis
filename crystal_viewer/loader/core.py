from __future__ import annotations

import base64
import copy
from collections import defaultdict
import os
import re
import tempfile
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Optional

import numpy as np
from molcrys_kit.utils.geometry import cart_to_frac

from .. import perf_log
from ..presets import get_default_catalog, workspace_root
from ..structure import molcrys_bridge
from ..scene import build_scene_from_atoms, legacy_scene, scene_json, scene_metadata, scene_ops


@dataclass
class LoadedCrystal:
    name: str
    title: str
    cif_path: str
    scene: Dict[str, Any]
    raw_atoms: list[dict[str, Any]] = field(default_factory=list)
    cell: Any | None = None
    M: Any | None = None
    view_direction: list[float] = field(default_factory=list)
    up: list[float] = field(default_factory=list)
    scene_cache: dict[tuple[str, bool], Dict[str, Any]] = field(default_factory=dict)
    pymatgen_structure: Any | None = None
    crystal: Any | None = None
    molcrys_analysis: Any | None = None
    formula_unit_atoms: list[dict[str, Any]] = field(default_factory=list)
    unwrapped_atoms: list[dict[str, Any]] = field(default_factory=list)
    unwrap_overflow: list[list[int]] = field(default_factory=list)
    fragment_table: list[dict[str, Any]] = field(default_factory=list)
    topology_fragment_table: list[dict[str, Any]] = field(default_factory=list)
    fragment_table_cache: dict[tuple[Any, ...], tuple[list[dict[str, Any]], list[str]]] = field(default_factory=dict)
    atom_fragment_labels: list[str] = field(default_factory=list)
    source: str = "catalog"
    # Per-bundle cache for scenes after a transforms pipeline has been
    # applied. Key is ``(display_mode, show_hydrogen, transforms_cache_key)``;
    # value is the post-transform scene dict (already including a refreshed
    # fragment_table). Lives here -- not on the global app -- so two
    # bundles served by the same Dash worker don't poison each other.
    _transformed_scene_cache: dict[tuple[Any, ...], Dict[str, Any]] = field(default_factory=dict)

    def metadata(self) -> Dict[str, Any]:
        meta = scene_metadata(self.scene)
        meta.update({
            "source": self.source,
            "fragment_count": len(self.topology_fragment_table or self.fragment_table),
            "has_topology": bool(self.topology_fragment_table or self.fragment_table),
            "parsed_atom_count": len(self.raw_atoms or []),
            "displayed_atom_count": len(self.scene.get("draw_atoms", []) or []),
            "asu_atom_count": len(self.raw_atoms or []),
        })
        return meta


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "uploaded"


def _unique_name(base: str, existing: Iterable[str]) -> str:
    existing_set = set(existing)
    if base not in existing_set:
        return base
    idx = 2
    while f"{base}_{idx}" in existing_set:
        idx += 1
    return f"{base}_{idx}"


def _infer_title_from_scene(scene: Dict[str, Any]) -> str:
    title = scene.get("title")
    if title:
        return str(title)
    return scene.get("name", "Uploaded Structure")


def build_empty_bundle(
    *,
    name: str = "__upload__",
    title: str = "Upload CIF to begin",
) -> LoadedCrystal:
    cell = SimpleNamespace(
        a=1.0,
        b=1.0,
        c=1.0,
        alpha=90.0,
        beta=90.0,
        gamma=90.0,
        volume=1.0,
    )
    M = np.eye(3, dtype=float)
    R = np.eye(3, dtype=float)
    scene = {
        "name": name,
        "title": title,
        "cell": cell,
        "M": M,
        "R": R,
        "view_x": np.array([1.0, 0.0, 0.0], dtype=float),
        "view_y": np.array([0.0, 1.0, 0.0], dtype=float),
        "view_z": np.array([0.0, 0.0, 1.0], dtype=float),
        "selected_atoms": [],
        "draw_atoms": [],
        "bonds": [],
        "label_items": [],
        "bounds": {
            "center": [0.0, 0.0, 0.0],
            "ranges": [1.0, 1.0, 1.0],
            "mins": [0.0, 0.0, 0.0],
            "maxs": [1.0, 1.0, 1.0],
            "screen_ranges": [1.0, 1.0, 1.0],
        },
        "camera": {
            "position": [0.0, 0.0, 8.0],
            "focal_point": [0.0, 0.0, 0.0],
            "up": [0.0, 1.0, 0.0],
        },
        "style": {},
        "show_hydrogen": False,
        "has_minor": False,
        "preset_entry": {},
        "display_mode": "formula_unit",
        "cif_path": None,
        "view_direction": np.array([0.0, 0.0, 1.0], dtype=float),
        "up": np.array([0.0, 1.0, 0.0], dtype=float),
        "fragment_table": [],
        "atom_fragment_labels": [],
        "unwrap_overflow": [],
    }
    return LoadedCrystal(
        name=name,
        title=title,
        cif_path="",
        scene=scene,
        raw_atoms=[],
        cell=cell,
        M=M,
        view_direction=[0.0, 0.0, 1.0],
        up=[0.0, 1.0, 0.0],
        scene_cache={("formula_unit", False): scene},
        fragment_table=[],
        topology_fragment_table=[],
        fragment_table_cache={("scene", "formula_unit", False): ([], [])},
        atom_fragment_labels=[],
        source="placeholder",
    )


DEFAULT_UNWRAP_MAX_ATOMS = 500


def _partial_occupancy_value(atom: dict[str, Any]) -> float:
    try:
        return float(atom.get("occ", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _blank_disorder_tags(atom: dict[str, Any]) -> bool:
    dg = str(atom.get("dg") or ".").strip()
    da = str(atom.get("da") or ".").strip()
    return dg in (".", "?", "") and da in (".", "?", "")


def _site_label(atom: dict[str, Any]) -> str:
    return str(atom.get("_asym_label") or atom.get("label") or "")


def _occupancy_disorder_label_stem(label: str) -> str:
    match = re.match(r"^([A-Za-z]+[0-9]+)", label)
    return match.group(1) if match else label


def _occupancy_only_disorder_indices(raw_atoms) -> set[int]:
    """Return indices likely belonging to blank-tag occupancy disorder.

    A single partial-occupancy site with blank disorder tags is ambiguous:
    it may be an ordered atom on a special position. SHELX occupancy-only
    disorder usually appears as sibling labels such as H3A/H3B or C8/C8A,
    so require at least two distinct source labels with the same stem before
    invoking the ordered-replica solver or tagging atoms minor.
    """
    by_stem: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for idx, atom in enumerate(raw_atoms):
        if _partial_occupancy_value(atom) >= 0.999 or not _blank_disorder_tags(atom):
            continue
        label = _site_label(atom)
        if not label:
            continue
        by_stem[_occupancy_disorder_label_stem(label)][label].append(idx)

    out: set[int] = set()
    for labels in by_stem.values():
        if len(labels) < 2:
            continue
        for indices in labels.values():
            out.update(indices)
    return out


def _explicit_assembly_disorder_indices(raw_atoms) -> set[int]:
    """Return indices of atoms participating in the third SHELX disorder
    shape: partial occupancy with non-blank
    ``_atom_site_disorder_assembly`` AND
    ``_atom_site_disorder_group``, restricted to assemblies that
    actually carry multiple competing groups.

    This is what Olex2 / SHELX honestly write for two-position disorder
    (HPEP: A/1+A/2, B/1+B/2, C/1+C/2 with paired occupancies summing to
    ~1.0). The older detector only looked for the SHELX "-PART"
    sign convention (``dg = "-1"``) and the blank-tag rotamer form, so
    HPEP-shaped CIFs rendered every disorder twin at full opacity --
    the user-visible "无序的透明度没了" bug.

    A single (assembly, group) bucket on its own (e.g. one
    half-occupied site sitting on a special position with a single
    explicit group) is *not* enough evidence to flag the atom as a
    disorder twin; we require at least two distinct group ids inside
    the same assembly so an ordered special-position site doesn't get
    mistaken for one half of a vanished partner.
    """
    by_assembly: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for idx, atom in enumerate(raw_atoms):
        if _partial_occupancy_value(atom) >= 0.999:
            continue
        dg = str(atom.get("dg") or ".").strip()
        da = str(atom.get("da") or ".").strip()
        if dg in (".", "?", "") or da in (".", "?", ""):
            continue
        by_assembly[da][dg].append(idx)
    out: set[int] = set()
    for groups in by_assembly.values():
        if len(groups) < 2:
            continue
        for indices in groups.values():
            out.update(indices)
    return out


def _has_explicit_assembly_disorder(raw_atoms) -> bool:
    return bool(_explicit_assembly_disorder_indices(raw_atoms))


def _has_shelx_occupancy_disorder(raw_atoms) -> bool:
    """Return True if ``raw_atoms`` contains any SHELX-style disorder
    that needs MolCrysKit's optimal-replica picker to resolve.

    Three patterns trigger this:

    1. *Occupancy-only* disorder: sibling labels with ``occ < 1`` and both
       ``_atom_site_disorder_group`` and ``_atom_site_disorder_assembly``
       blank (both default to "."). SHELX often writes rotamer pairs
       (NH4+ in DAP-4, perchlorate H atoms in SY's older revisions)
       this way, summing two occupancies to 1 with no other markers.
    2. *SHELX -PART convention*: ``occ < 1`` with ``dg`` starting with
       ``"-"`` (e.g. ``"-1"``). The other half of the disorder pair
       lives at the symmetry equivalent of this atom; after
       :func:`parse_asu` symmetry expansion both alternatives are
       present as separate atoms, but neither carries the marker that
       distinguishes them. MolCrysKit's neighbour-list will then bond
       the two alternatives together (N3 / N2 at 0.15 A apart in SY),
       fusing two chemically distinct cations into one species.
    3. *Explicit assembly + group* disorder: ``occ < 1`` with non-blank
       ``_atom_site_disorder_assembly`` AND ``_atom_site_disorder_group``
       where the same assembly has multiple competing groups (Olex2's
       standard A/1+A/2, B/1+B/2 form, e.g. HPEP). Without this branch
       the loader silently treats both alternatives as full-occupancy
       major atoms and the disorder='opacity' style has nothing to fade.

    In all three cases ``parse_asu`` alone cannot resolve the disorder,
    so we hand the CIF to MolCrysKit's optimal-replica picker and tag
    the non-chosen alternates with ``_is_minor=True`` so the bond graph
    sees one consistent set per disorder site.
    """
    for atom in raw_atoms:
        occ = _partial_occupancy_value(atom)
        if occ >= 0.999:
            continue
        dg = str(atom.get("dg") or ".").strip()
        if dg.startswith("-") and dg not in ("-",):
            return True
    if _has_explicit_assembly_disorder(raw_atoms):
        return True
    return bool(_occupancy_only_disorder_indices(raw_atoms))


def _tag_shelx_occupancy_disorder(raw_atoms, cif_path: str, M):
    """If ``raw_atoms`` contains SHELX-style occupancy disorder,
    consult :func:`molcrys_kit.analysis.disorder.\
generate_ordered_replicas_from_disordered_sites` for the optimal
    rotamer choice and tag every non-chosen disorder image with
    ``_is_minor=True``.

    The renderer continues to draw every atom; minor images are just
    faded to ``disorder_alpha=0.22`` and the fragment-table builder
    drops them so the stoichiometry / coordination polyhedron analysis
    sees a single chemically sensible structure.

    MolCrysKit exposes the selected source-site indices via
    ``return_kept_indices=True``; MatterVis only mirrors that selection
    onto its raw atom dicts for rendering.

    All steps are wrapped in a single ``try`` block: if MolCrysKit
    can't resolve the disorder for any reason (missing dependency,
    parser error, CIF rejected by ``scan_cif_disorder``) we leave
    ``raw_atoms`` untouched. Blank partial occupancy alone is not enough
    evidence to mark atoms minor because ordered special-position sites
    use the same CIF shape.
    """
    if not _has_shelx_occupancy_disorder(raw_atoms):
        return raw_atoms

    try:
        try:
            from molcrys_kit.analysis.disorder import (
                generate_ordered_replicas_from_disordered_sites,
            )

            replicas = generate_ordered_replicas_from_disordered_sites(
                cif_path, method="optimal", return_kept_indices=True
            )
        except Exception:
            return raw_atoms
        if not replicas:
            return raw_atoms
        first = replicas[0]
        if not isinstance(first, tuple) or len(first) != 2:
            return raw_atoms
        _crystal, kept_indices = first
        # Bridge MCK's DisorderInfo index space → raw_atoms via geometric
        # matching.  The two CIF parsers expand symmetry independently,
        # so their index spaces are NOT aligned (see disorder_index.py).
        from ..structure.disorder_index import map_mck_indices_to_raw

        idx_map = map_mck_indices_to_raw(cif_path, raw_atoms, kept_indices)
        kept_raw = set(idx_map.values())
        out = [dict(atom) for atom in raw_atoms]

        disordered_idx: list[int] = []
        occupancy_only_idx = _occupancy_only_disorder_indices(out)
        explicit_assembly_idx = _explicit_assembly_disorder_indices(out)
        for idx, atom in enumerate(out):
            occ = _partial_occupancy_value(atom)
            if occ >= 0.999 or "_is_minor" in atom:
                continue
            dg = str(atom.get("dg") or ".").strip()
            if (
                idx in occupancy_only_idx
                or idx in explicit_assembly_idx
                or (dg.startswith("-") and dg not in ("-",))
            ):
                disordered_idx.append(idx)

        if not disordered_idx:
            return out

        # --- Validate MCK's choice against crystallographic occupancy ---
        # For explicit-assembly disorder (CIFs with non-blank
        # _atom_site_disorder_assembly + _atom_site_disorder_group),
        # the "kept" (major) set MUST correspond to the higher-
        # occupancy group — this is a hard crystallographic invariant.
        # MCK's 'optimal' method uses structural/connectivity criteria
        # that can contradict occupancy (observed on GAGCIF01: it kept
        # occ=0.474 B over occ=0.526 A, rendering both cations as
        # semi-transparent).  When the choice is inverted, correct it
        # and log a warning.
        if explicit_assembly_idx:
            kept_occs = [
                _partial_occupancy_value(out[i])
                for i in disordered_idx
                if i in kept_raw and i in explicit_assembly_idx
            ]
            disc_occs = [
                _partial_occupancy_value(out[i])
                for i in disordered_idx
                if i not in kept_raw and i in explicit_assembly_idx
            ]
            if kept_occs and disc_occs:
                avg_kept = sum(kept_occs) / len(kept_occs)
                avg_disc = sum(disc_occs) / len(disc_occs)
                if avg_kept < avg_disc - 1e-4:
                    import logging

                    logging.getLogger(__name__).warning(
                        "MCK optimal-replica chose lower-occupancy group "
                        "(avg_kept=%.4f < avg_disc=%.4f) for %s; "
                        "overriding with occupancy-based assignment.",
                        avg_kept,
                        avg_disc,
                        cif_path,
                    )
                    # Flip: for explicit-assembly atoms use occupancy
                    # to determine major/minor directly.
                    kept_raw = {
                        i for i in disordered_idx
                        if i not in kept_raw and i in explicit_assembly_idx
                    } | (kept_raw - explicit_assembly_idx)

        # Explicit major / minor labels on every disordered atom: a
        # chosen atom must have ``_is_minor=False`` set (NOT just
        # absent) because ``_is_minor_atom`` falls through to the
        # ``dg.startswith("-")`` SHELX-PART heuristic when the flag is
        # missing -- that heuristic would otherwise re-classify the
        # chosen N3 / N2 atoms as minor and the bond graph would lose
        # the en cation entirely (the "C2N2 missing" SY bug).
        for idx in disordered_idx:
            if idx in kept_raw:
                out[idx]["_is_minor"] = False
                out[idx]["_mv_auto_disorder_assembly"] = "mv_auto"
                out[idx]["_mv_auto_disorder_group"] = "1"
            else:
                out[idx]["_is_minor"] = True
                out[idx]["_mv_auto_disorder_assembly"] = "mv_auto"
                out[idx]["_mv_auto_disorder_group"] = "2"
        return out
    except Exception:
        return raw_atoms


def _unwrapped_atoms_from_atoms(
    atoms,
    cell,
    M,
    *,
    include_minor: bool = True,
    max_atoms: int | None = DEFAULT_UNWRAP_MAX_ATOMS,
    molcrys_analysis: Any,
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    """Return per-atom positions that are continuous across periodic
    boundaries, so a molecule that straddles a cell face renders as a
    single contiguous fragment instead of two halves drifting apart.

    ``molcrys_analysis`` is required: the unwrapping always goes
    through :class:`MolCrysKit`'s already-computed
    ``mol_indices`` / ``mol_cart_positions``. Those are the canonical
    BFS unwrap result that the formula-unit / topology pipelines also
    trust, and doing it again with a homegrown ``find_bonds(cell=cell)``
    call was historically buggy on monoclinic cells (the MPEP
    regression where a ring crossing the cell boundary was rendered as
    two halves -- the legacy KDTree pre-filter ignored PBC, so the
    cross-cell bond that should have stitched the ring back together
    never made it into the bond graph).

    The legacy fallback that re-derived bonds via
    ``ops.find_bonds(cell=cell)`` was removed in the
    "loader-mol-indices" refactor: every production caller has a
    :class:`molcrys_bridge.CrystalAnalysis` handle (it's built once
    per CIF in :func:`build_loaded_crystal`), and synthetic-atom
    test fixtures should call :func:`molcrys_bridge.analyze` first
    rather than relying on the legacy path that historically
    misbehaved on disorder + PBC special positions.
    """
    if molcrys_analysis is None:
        raise TypeError(
            "_unwrapped_atoms_from_atoms now requires a molcrys_analysis "
            "argument. Call molcrys_bridge.analyze(atoms, M) first and "
            "pass the result, or use build_loaded_crystal which wires "
            "this up automatically."
        )
    return _unwrapped_atoms_from_molcrys(atoms, M, molcrys_analysis, include_minor=include_minor)


def _unwrapped_atoms_from_molcrys(
    atoms,
    M,
    molcrys_analysis,
    *,
    include_minor: bool = True,
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    """Build the unwrapped-atom list directly from a
    :class:`molcrys_bridge.CrystalAnalysis`. ``mol_indices`` /
    ``mol_cart_positions`` were already produced by MolCrysKit's
    PBC-aware ASE neighbour-list traversal; copy those Cartesian
    positions onto the source atom dicts and recompute the matching
    fractional coords. Atoms not touched by any molecule (e.g.
    isolated minor-disorder ghosts that the analysis dropped) keep
    their original cart/frac.
    """
    out = [dict(atom) for atom in atoms]
    for idx, atom in enumerate(out):
        atom["_unwrapped"] = False
        atom["_source_index"] = int(idx)

    M_arr = np.asarray(M, dtype=float)

    mol_indices = getattr(molcrys_analysis, "mol_indices", None) or []
    mol_cart_positions = getattr(molcrys_analysis, "mol_cart_positions", None) or []

    # Per-axis cell lengths for framework detection
    _cell_lengths = np.array([np.linalg.norm(M_arr[i]) for i in range(3)])

    for mol_idx, (indices, cart_positions) in enumerate(zip(mol_indices, mol_cart_positions)):
        coords = np.asarray(cart_positions, dtype=float)
        if coords.ndim != 2 or coords.shape[0] != len(indices):
            continue

        # Skip unwrapping for framework/network molecules: if the molecule
        # span exceeds 90% of any cell axis length, it is a framework that
        # wraps across PBC and should NOT be unwrapped.
        span = coords.max(axis=0) - coords.min(axis=0)
        # Project span onto each cell axis
        frac_span = span @ np.linalg.inv(M_arr)
        if np.any(np.abs(frac_span) > 0.9):
            continue

        for local_idx, raw_idx in enumerate(indices):
            if raw_idx < 0 or raw_idx >= len(out):
                continue
            cart = coords[local_idx]
            # Keep the crystallographic wrapped position as the boundary
            # image key. ``frac`` below is overwritten with MCK's continuous
            # molecule coordinate, which may be outside [0, 1] for fragments
            # crossing a face; boundary replication must still be based on
            # the original special-position / face membership.
            out[raw_idx]["_wrapped_frac"] = np.asarray(out[raw_idx].get("frac"), dtype=float).copy()
            out[raw_idx]["_source_molecule_index"] = int(mol_idx)
            out[raw_idx]["cart"] = cart.copy()
            out[raw_idx]["frac"] = cart_to_frac(cart, M_arr)
            out[raw_idx]["_unwrapped"] = True

    if not include_minor:
        ops = scene_ops()
        out = [atom for atom in out if not ops.is_minor(atom)]

    # MolCrysKit handles oversize fragments internally (it never returns
    # a partial molecule); there is no overflow component to surface.
    return out, []


def _fragment_table_from_atoms(
    bundle_name: str,
    atoms,
    cell,
    M,
    *,
    molcrys_analysis,
    use_source_indices: bool = True,
    include_minor: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build the per-fragment table directly from MolCrysKit's
    ``mol_indices`` -- the canonical molecule grouping.

    The legacy implementation re-derived this by calling
    ``ops.find_bonds(atom_pool, cell=cell)`` and clustering the result.
    On structures with SHELX-style occupancy disorder (DAP-4 NH4+,
    SY perchlorate H atoms) and atoms on special positions
    (face / edge / corner), that path produced:

        * 18× "?" orphan-H fragments (Bug 3)
        * 8× NH4+ split into cluster_size = 2/3/5 instead of 5 (Bug 2)
        * "N", "NH", "NH4", "NH8" fragments for chemically equivalent
          ammonium cations (Bug 5/6)

    MolCrysKit's bond graph already handles PBC special positions and
    multi-image disorder correctly; this function now just *formats*
    that grouping into the per-fragment dict the renderer / topology
    code expects. ``molcrys_analysis`` is required -- there is no
    fallback to ``ops.find_bonds`` any more.

    ``atoms`` may be ``raw_atoms`` (one-to-one with raw indices) or a
    subset/translated copy thereof (each atom carrying a
    ``_source_index`` field pointing back to its raw_atoms position).
    For atoms missing ``_source_index`` we fall back to their position
    in ``atoms``; that's correct only when ``atoms is raw_atoms``.
    """
    ops = scene_ops()
    mol_indices = getattr(molcrys_analysis, "mol_indices", None) or []
    if not atoms:
        return [], []

    # Build (image_shift, raw_index) -> local_index map. Atoms produced
    # by ``replicate_atoms`` (repeat / supercell transforms) share a
    # ``_source_index`` across replicas but have distinct
    # ``_image_shift`` tuples; keying on the pair preserves one
    # fragment-table row per replica instead of collapsing them.
    image_to_local: dict[tuple[tuple[int, int, int], int], int] = {}
    pool_kept: list[dict[str, Any]] = []
    pool_source_idx: list[int] = []
    for local_idx, atom in enumerate(atoms):
        if ops.is_minor(atom) and not include_minor:
            continue
        raw_idx = int(atom.get("_source_index", local_idx))
        shift = atom.get("_image_shift") or (0, 0, 0)
        shift_key = tuple(int(x) for x in shift)
        image_to_local[(shift_key, raw_idx)] = len(pool_kept)
        pool_kept.append(dict(atom))
        pool_source_idx.append(local_idx if not use_source_indices else raw_idx)

    if not pool_kept:
        return [], []

    # Group atoms by (image_shift, mol_index_k). Each replica image of a
    # MolCrysKit molecule becomes its own fragment-table row.
    components: list[tuple[list[int], int | None]] = []
    seen_local: set[int] = set()
    seen_images = sorted({key[0] for key in image_to_local})
    for shift_key in seen_images:
        for mol_index, indices in enumerate(mol_indices):
            component = []
            for raw_idx in indices:
                local = image_to_local.get((shift_key, int(raw_idx)))
                if local is None or local in seen_local:
                    continue
                component.append(local)
                seen_local.add(local)
            if component:
                components.append((sorted(component), int(mol_index)))
    # Sweep for any kept atom that didn't make it into a molecule component.
    # These should be rare now that MCK's bond perception is disorder-aware
    # and returns both major and minor alternatives as whole fragments; keep
    # singleton rows only as diagnostics for genuinely uncovered atoms.
    for local_idx in range(len(pool_kept)):
        if local_idx not in seen_local:
            components.append(([local_idx], None))
            seen_local.add(local_idx)

    fragments = []
    for component, mol_index in components:
        site_indices = sorted(pool_source_idx[idx] for idx in component)
        component_atoms = [pool_kept[idx] for idx in component]
        heavy_atoms = [atom for atom in component_atoms if atom["elem"] != "H"]
        center_atoms = heavy_atoms or component_atoms
        elem_set = {atom["elem"] for atom in heavy_atoms}
        if not center_atoms:
            continue
        center_cart = np.mean([atom["cart"] for atom in center_atoms], axis=0)
        center_frac = np.mean([atom["frac"] for atom in center_atoms], axis=0)
        # Disorder-aware heavy-atom counts: atoms that belong to the
        # same SHELX disorder assembly (e.g. PEP's C1/C1A pair, both
        # ``da="B"`` with ``dg`` 1 vs 2) collapse to one chemical
        # carbon, so the displayed formula matches what the molecule
        # actually contains rather than counting both alternatives.
        elem_counts: dict[str, int] = {}
        assemblies: dict[tuple[str, str], dict[str, int]] = {}
        for atom in heavy_atoms:
            elem = atom["elem"]
            da = str(atom.get("da") or ".").strip()
            dg = str(atom.get("dg") or ".").strip()
            if da in ("", ".", "?"):
                elem_counts[elem] = elem_counts.get(elem, 0) + 1
                continue
            bucket = assemblies.setdefault((elem, da), {})
            bucket[dg] = bucket.get(dg, 0) + 1
        for (elem, _da), bucket in assemblies.items():
            elem_counts[elem] = elem_counts.get(elem, 0) + max(bucket.values())
        # Hill-ish ordering for the public formula: C, N, then alphabetical.
        # (Pure mineral fragments without C come out alphabetical.) The result
        # is a stable string identifier we can group on across A/B/X labels --
        # e.g. "C8N1" is the DAP-4 DABCO ring; "N1" is the NH4+.
        ordered: list[tuple[str, int]] = []
        for elem in ("C", "N"):
            if elem in elem_counts:
                ordered.append((elem, elem_counts.pop(elem)))
        ordered.extend(sorted(elem_counts.items()))
        formula = "".join(f"{elem}{count}" if count > 1 else elem for elem, count in ordered) or "?"
        fragments.append({
            "site_indices": site_indices,
            "source_molecule_index": mol_index,
            "center": [float(x) for x in center_cart],
            "frac_center": [float(x) for x in center_frac],
            "elem_set": sorted(elem_set),
            "heavy_atom_count": len(heavy_atoms),
            "cluster_size": len(component_atoms),
            "species": "".join(sorted(elem_set)) or "?",
            "formula": formula,
        })

    x_fragments = [frag for frag in fragments if "Cl" in frag["elem_set"]]
    non_x = [frag for frag in fragments if frag not in x_fragments]

    # A vs B classification follows the molecular-perchlorate convention
    # A2B(ClO4)4: B is the *smaller* non-X cluster. This handles three cases:
    #   1. Real metal B-site: single heavy atom (size = 1) is the smallest by
    #      definition -> B. Organic cations are bigger -> A.
    #   2. Pure organic salt with two distinct cation sizes (e.g. PEP has
    #      heavy=4 and heavy=6 cations): smallest -> B, larger -> A.
    #   3. Pure organic salt with a single cation type (e.g. DAP-4 has two
    #      identical heavy=8 cations): only one size class exists -> all A.
    # Non-organic, non-X clusters (e.g. lone halide counterions) fall through
    # to "?" so they don't pollute either A or B.
    NON_METAL_HEAVY = {
        "H", "B", "C", "N", "O", "F",
        "Si", "P", "S", "Cl",
        "Ge", "As", "Se", "Br",
        "Sb", "Te", "I",
    }
    organic_or_metal = []
    for frag in non_x:
        elems = set(frag["elem_set"])
        is_single_metal = frag["heavy_atom_count"] == 1 and not (elems & NON_METAL_HEAVY)
        is_organic = bool(elems & {"C", "N"})
        if is_single_metal or is_organic:
            organic_or_metal.append(frag)
        else:
            frag["type"] = "?"
    if organic_or_metal:
        sizes = sorted({frag["heavy_atom_count"] for frag in organic_or_metal})
        if len(sizes) >= 2:
            smallest = sizes[0]
            for frag in organic_or_metal:
                frag["type"] = "B" if frag["heavy_atom_count"] == smallest else "A"
        else:
            for frag in organic_or_metal:
                frag["type"] = "A"
    for frag in x_fragments:
        frag["type"] = "X"

    type_order = {"B": 0, "A": 1, "X": 2, "?": 3}
    fragments.sort(
        key=lambda frag: (
            type_order.get(frag["type"], 9),
            *[float(x % 1.0) for x in frag["frac_center"]],
            frag["heavy_atom_count"],
            frag["cluster_size"],
        )
    )

    counters: dict[str, int] = defaultdict(int)
    atom_fragment_labels = ["?"] * len(atoms)
    final_table = []
    for frag_idx, frag in enumerate(fragments):
        frag_type = frag["type"]
        label_index = counters[frag_type]
        counters[frag_type] += 1
        for site_idx in frag["site_indices"]:
            atom_fragment_labels[site_idx] = frag_type
        final_table.append({
            "index": frag_idx,
            "type": frag_type,
            "label": f"{frag_type}{label_index}",
            "species": frag["species"],
            "formula": frag.get("formula"),
            "elem_set": frag.get("elem_set", []),
            "center": frag["center"],
            "frac_center": frag["frac_center"],
            "site_indices": frag["site_indices"],
            "source_molecule_index": frag.get("source_molecule_index"),
            "source": bundle_name,
            "heavy_atom_count": frag["heavy_atom_count"],
            "cluster_size": frag["cluster_size"],
        })
    return final_table, atom_fragment_labels


def build_bundle_scene(
    bundle: LoadedCrystal,
    *,
    display_mode: str = "formula_unit",
    show_hydrogen: bool = False,
    preset: Optional[Dict[str, Any]] = None,
    transforms: Optional[list[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the scene dict for ``bundle``.

    ``transforms`` is an optional list of transform-spec dicts (see
    :mod:`crystal_viewer.transforms`); when present the base scene is
    built once (and cached) then transforms are composed on top, with
    the post-transform fragment table re-derived from the manifested
    atom list. The base scene cache is unchanged so toggling transforms
    on/off stays cheap.
    """
    base_cache_key = (display_mode, bool(show_hydrogen))
    base_scene = bundle.scene_cache.get(base_cache_key)
    if base_scene is None:
        perf_log.record(
            "cache:scene",
            kind="cache",
            info={"hit": False, "display_mode": display_mode, "hydrogens": bool(show_hydrogen)},
        )
        ops = scene_ops()
        view_dir = np.array(bundle.view_direction, dtype=float)
        up = np.array(bundle.up, dtype=float)
        R = ops.view_rotation(view_dir, up)
        base_scene = build_scene_from_atoms(
            name=bundle.name,
            title=bundle.title,
            atoms=bundle.raw_atoms,
            cell=bundle.cell,
            M=bundle.M,
            R=R,
            show_hydrogen=show_hydrogen,
            preset=preset,
            display_mode=display_mode,
            ops=ops,
            formula_unit_atoms=bundle.formula_unit_atoms if display_mode == "formula_unit" else None,
            unwrapped_atoms=bundle.unwrapped_atoms,
        )
        base_scene["cif_path"] = bundle.cif_path
        base_scene["view_direction"] = view_dir
        base_scene["up"] = up
        base_scene["unwrap_overflow"] = copy.deepcopy(bundle.unwrap_overflow)
        fragment_cache_key = ("scene", display_mode, bool(show_hydrogen))
        cached_fragments = bundle.fragment_table_cache.get(fragment_cache_key)
        if cached_fragments is None:
            fragment_table, atom_fragment_labels = _fragment_table_from_atoms(
                bundle.name,
                base_scene["draw_atoms"],
                base_scene["cell"],
                base_scene["M"],
                molcrys_analysis=bundle.molcrys_analysis,
                use_source_indices=False,
                include_minor=True,
            )
            bundle.fragment_table_cache[fragment_cache_key] = (
                copy.deepcopy(fragment_table),
                list(atom_fragment_labels),
            )
        else:
            fragment_table = copy.deepcopy(cached_fragments[0])
            atom_fragment_labels = list(cached_fragments[1])
        base_scene["fragment_table"] = fragment_table
        base_scene["atom_fragment_labels"] = atom_fragment_labels
        bundle.scene_cache[base_cache_key] = base_scene
    else:
        perf_log.record(
            "cache:scene",
            kind="cache",
            info={"hit": True, "display_mode": display_mode, "hydrogens": bool(show_hydrogen)},
        )

    if not transforms:
        return base_scene

    from ..transforms import apply_transforms, transforms_cache_key

    transformed_cache = getattr(bundle, "_transformed_scene_cache", None)
    if transformed_cache is None:
        transformed_cache = {}
        try:
            bundle._transformed_scene_cache = transformed_cache
        except Exception:
            pass
    cache_key = (display_mode, bool(show_hydrogen), transforms_cache_key(transforms))
    cached = transformed_cache.get(cache_key) if isinstance(transformed_cache, dict) else None
    if cached is not None:
        return cached

    transformed = apply_transforms(
        base_scene,
        transforms,
        bundle=bundle,
        style=base_scene.get("style"),
    )
    if transformed is base_scene:
        return base_scene
    transformed = dict(transformed)
    if (
        not transformed.get("fragment_table")
        or len(transformed.get("atom_fragment_labels") or []) != len(transformed.get("draw_atoms") or [])
    ):
        fragment_table, atom_fragment_labels = _fragment_table_from_atoms(
            bundle.name,
            transformed["draw_atoms"],
            transformed.get("cell") or base_scene["cell"],
            transformed.get("M") if transformed.get("M") is not None else base_scene["M"],
            molcrys_analysis=bundle.molcrys_analysis,
            use_source_indices=False,
            include_minor=True,
        )
        transformed["fragment_table"] = fragment_table
        transformed["atom_fragment_labels"] = atom_fragment_labels
    transformed["cif_path"] = base_scene.get("cif_path")
    transformed["view_direction"] = base_scene.get("view_direction")
    transformed["up"] = base_scene.get("up")
    transformed["unwrap_overflow"] = []
    if isinstance(transformed_cache, dict):
        transformed_cache[cache_key] = transformed
    return transformed


# Diagonal viewing direction used as the upload fallback when no preset
# matches. Matches Plotly's default (1.25, 1.25, 1.25) eye direction with
# +c as the screen-up axis, so any non-cubic cell still shows depth
# instead of collapsing into a flat ab-plane projection. Stored as a
# unit vector so ``view_rotation`` doesn't need to renormalise.
_UPLOAD_DEFAULT_VIEW_DIR = np.array([1.0, 1.0, 1.0], dtype=float) / np.sqrt(3.0)
_UPLOAD_DEFAULT_UP = np.array([0.0, 0.0, 1.0], dtype=float)


def _upload_default_view(name: str, preset: Optional[Dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Pick an initial ``(view_direction, up)`` for an uploaded CIF.

    Tries an exact preset entry first, then a stem match (so ``SY_3``
    honours the ``SY`` preset), then falls back to a 3D-friendly
    diagonal so elongated cells don't render as flat 2D projections.
    """
    structures = (preset or {}).get("structures", {}) if isinstance(preset, dict) else {}
    candidates = [name]
    # ``infer_uploaded_name`` appends ``_2``, ``_3``, ... when a name
    # collides; strip the suffix so the original preset still applies.
    stem_match = re.match(r"^(?P<stem>.+?)(?:_\d+)?$", name)
    if stem_match:
        stem = stem_match.group("stem")
        if stem and stem != name:
            candidates.append(stem)
    for candidate in candidates:
        entry = structures.get(candidate) if isinstance(structures, dict) else None
        if not isinstance(entry, dict):
            continue
        camera = entry.get("camera") if isinstance(entry.get("camera"), dict) else None
        if camera and camera.get("position") and camera.get("focal_point") and camera.get("up"):
            view_dir, up = legacy_scene.scene_from_camera(
                camera["position"], camera["focal_point"], camera["up"]
            )
            return np.asarray(view_dir, dtype=float), np.asarray(up, dtype=float)
        view_direction = entry.get("view_direction")
        if view_direction:
            up = entry.get("up", [0.0, 0.0, 1.0])
            return np.asarray(view_direction, dtype=float), np.asarray(up, dtype=float)
    return _UPLOAD_DEFAULT_VIEW_DIR.copy(), _UPLOAD_DEFAULT_UP.copy()


def build_loaded_crystal(
    *,
    name: str,
    cif_path: str,
    title: Optional[str] = None,
    preset: Optional[Dict[str, Any]] = None,
    source: str = "catalog",
) -> LoadedCrystal:
    # Each sub-block is wrapped in a ``perf_log.time_block`` so the
    # /api/v1/perf endpoint shows exactly which leg of an upload is
    # slow (CIF parse vs. molcryskit analysis vs. bond perception
    # vs. fragment-table build). See ``crystal_viewer.perf_log``.
    from . import perf_log

    ops = scene_ops()
    preset = preset or {}
    with perf_log.time_block("loader:parse_asu", kind="event", structure=name, cif_path=cif_path):
        raw_atoms, cell, legacy_M = ops.parse_asu(cif_path)
        M = np.asarray(legacy_M, dtype=float).T
    with perf_log.time_block(
        "loader:resolve_shelx_disorder",
        kind="event",
        structure=name,
        cif_path=cif_path,
    ):
        raw_atoms = _tag_shelx_occupancy_disorder(raw_atoms, cif_path, M)
    n_atoms = len(raw_atoms) if raw_atoms is not None else 0
    with perf_log.time_block(
        "loader:molcrys_analyze",
        kind="event",
        structure=name,
        n_atoms=n_atoms,
    ):
        molcrys_analysis = molcrys_bridge.analyze(raw_atoms, M)
    with perf_log.time_block("loader:select_formula_unit", kind="event", structure=name):
        formula_unit_atoms = molcrys_bridge.select_formula_unit(raw_atoms, M, analysis=molcrys_analysis)
    with perf_log.time_block("loader:unwrap_atoms", kind="event", structure=name):
        unwrapped_atoms, unwrap_overflow = _unwrapped_atoms_from_atoms(
            raw_atoms,
            cell,
            M,
            include_minor=True,
            molcrys_analysis=molcrys_analysis,
        )
    # ``_resolve_view`` is happy to short-circuit on a preset entry
    # (camera or view_direction explicitly provided) but otherwise
    # falls through to ``ops.auto_view_dir`` which scores >1000 view
    # candidates by ray-projecting every heavy atom -- ~12 s for a
    # 1024-atom unit cell. Uploaded CIFs almost never have a preset
    # by their unique name (``SY_3``, ``upload_2``, ...), so the user
    # paid that cost on every upload. We use a 3D-friendly diagonal
    # default (eye along (1,1,1), up=+c) instead of straight +z --
    # the latter projects elongated cells (e.g. SY's 8 x 25 x 10) to
    # a tall, depthless rectangle that users perceive as "flat".
    # Preset entries (catalog or user-supplied) still win, including
    # a stem-match fallback so ``SY_3`` honours the ``SY`` preset.
    is_upload = source == "upload"
    if is_upload:
        with perf_log.time_block("loader:default_view", kind="event", structure=name, reason="skip_auto_view_for_upload"):
            view_dir, up = _upload_default_view(name, preset)
    else:
        with perf_log.time_block("loader:resolve_view", kind="event", structure=name):
            view_dir, up = legacy_scene._resolve_view(ops, name, raw_atoms, legacy_M, cell, preset)
    R = ops.view_rotation(view_dir, up)
    final_title = title or name
    with perf_log.time_block(
        "loader:build_scene_from_atoms",
        kind="event",
        structure=name,
        n_atoms=n_atoms,
    ):
        initial_scene = build_scene_from_atoms(
            name=name,
            title=final_title,
            atoms=raw_atoms,
            cell=cell,
            M=M,
            R=R,
            preset=preset,
            show_hydrogen=False,
            display_mode="formula_unit",
            ops=ops,
            formula_unit_atoms=formula_unit_atoms,
            unwrapped_atoms=unwrapped_atoms,
        )
    initial_scene["cif_path"] = cif_path
    initial_scene["view_direction"] = np.array(view_dir, dtype=float)
    initial_scene["up"] = np.array(up, dtype=float)
    initial_scene["unwrap_overflow"] = copy.deepcopy(unwrap_overflow)
    with perf_log.time_block(
        "loader:fragment_table_scene",
        kind="event",
        structure=name,
    ):
        fragment_table, atom_fragment_labels = _fragment_table_from_atoms(
            name,
            initial_scene["draw_atoms"],
            initial_scene["cell"],
            initial_scene["M"],
            molcrys_analysis=molcrys_analysis,
            use_source_indices=False,
            include_minor=True,
        )
    initial_scene["fragment_table"] = fragment_table
    initial_scene["atom_fragment_labels"] = atom_fragment_labels
    with perf_log.time_block(
        "loader:fragment_table_topology",
        kind="event",
        structure=name,
    ):
        # ``include_minor=False`` here is deliberate: the topology
        # fragment table summarises the *chemical* contents of the
        # cell, not the atoms drawn on screen. Minor disorder images
        # (the discarded alternative orientation that
        # ``_tag_shelx_occupancy_disorder`` flagged) are not part of
        # any real molecule once MolCrysKit's bond perception has run
        # without them; including them would pollute the table with
        # singleton "?" fragments for every orphan H / C / N of the
        # rejected orientation. The renderer still draws those atoms
        # faded; only the analysis table hides them.
        topology_fragment_table, _ = _fragment_table_from_atoms(
            name,
            raw_atoms,
            cell,
            M,
            molcrys_analysis=molcrys_analysis,
            use_source_indices=True,
            include_minor=False,
        )
    fragment_table_cache = {
        ("scene", "formula_unit", False): (
            copy.deepcopy(fragment_table),
            list(atom_fragment_labels),
        ),
        ("topology",): (
            copy.deepcopy(topology_fragment_table),
            [],
        ),
    }

    bundle = LoadedCrystal(
        name=name,
        title=final_title,
        cif_path=cif_path,
        scene=initial_scene,
        raw_atoms=[dict(atom) for atom in raw_atoms],
        cell=cell,
        M=M,
        view_direction=np.array(view_dir, dtype=float).tolist(),
        up=np.array(up, dtype=float).tolist(),
        crystal=molcrys_analysis.crystal,
        molcrys_analysis=molcrys_analysis,
        formula_unit_atoms=[dict(atom) for atom in formula_unit_atoms],
        unwrapped_atoms=[dict(atom) for atom in unwrapped_atoms],
        unwrap_overflow=[list(component) for component in unwrap_overflow],
        scene_cache={("formula_unit", False): initial_scene},
        fragment_table=fragment_table,
        topology_fragment_table=topology_fragment_table,
        fragment_table_cache=fragment_table_cache,
        atom_fragment_labels=atom_fragment_labels,
        source=source,
    )
    return bundle


