from __future__ import annotations

import copy
import os
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart  # noqa: F401

from .. import perf_log
from ..structure.bonds import bonds_conflict
from ..structure.cif_parse import load_cif, parse_asu
from ..structure import molcrys_bridge
from ..style.disorder import (
    atom_is_disordered,
    atom_is_minor,
    bond_is_disordered,
    bond_is_minor,
    disorder_alpha,
    is_minor,
)
from ..structure.formula_unit import select_formula_unit
from ..structure.geometry import _nearest_pbc_cart, view_rotation
from ..style.palette import atom_r, elem_color, elem_color_light
from ..presets import DEFAULT_STYLE, deep_merge, default_preset, json_safe  # noqa: F401
from ..viewpoint import auto_view_dir
from ..legacy.plot_crystal import _compute_label_positions

# ── New layered modules ──────────────────────────────────────────────
from ..render.display_modes import selected_atoms_for_mode
from ..render.boundary_replicas import expand_boundary_replicas

# Re-export serialization / style helpers from their canonical homes.
from .serialize import (  # noqa: F401
    _to_builtin,
    scene_json,
    scene_metadata,
)
from .style import (
    _resolve_element_color,  # noqa: F401
    apply_element_colors,
    merge_structure_style,
    rebuild_scene_with_style,
    scene_style,
)

# Legacy aliases for callers that imported the private helpers by name.
# These are now defined in render/display_modes.py and
# render/boundary_replicas.py respectively.
_selected_atoms_for_mode = selected_atoms_for_mode
_expand_boundary_replicas = expand_boundary_replicas


PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_DIR = os.path.dirname(PACKAGE_DIR)
from ..legacy import crystal_scene as legacy_scene  # noqa: E402

__all__ = [
    "apply_element_colors",
    "build_scene_from_atoms",
    "build_scene_from_cif",
    "merge_structure_style",
    "rebuild_scene_with_style",
    "scene_json",
    "scene_metadata",
    "scene_ops",
    "scene_style",
    "_expand_boundary_replicas",
    "_selected_atoms_for_mode",
    "_to_builtin",
]


def scene_ops():
    return SimpleNamespace(
        parse_asu=parse_asu,
        select_formula_unit=select_formula_unit,
        auto_view_dir=auto_view_dir,
        view_rotation=view_rotation,
        disorder_alpha=disorder_alpha,
        is_minor=is_minor,
        elem_color=elem_color,
        elem_color_light=elem_color_light,
        atom_r=atom_r,
        compute_label_positions=_compute_label_positions,
    )


def _bond_endpoints(ai, aj, cell, display_mode: str):
    start = np.array(ai["cart"], dtype=float)
    if ai.get("_strict_unit_cell") or aj.get("_strict_unit_cell"):
        return start, np.array(aj["cart"], dtype=float)
    if display_mode in ("formula_unit", "cluster") or (
        ai.get("_unwrapped") and aj.get("_unwrapped")
    ):
        end = np.array(aj["cart"], dtype=float)
    else:
        end = np.array(_nearest_pbc_cart(ai["cart"], aj["cart"], cell), dtype=float)
    return start, end


_SOURCE_IMAGE_TOL = 1e-5


def _manifest_strict_bonded_images(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    M: Any,
    canonical_bond_records: list[dict[str, Any]],
) -> int:
    """Add complete periodic images required by strict-cell bonds.

    A strict-cell image is introduced because one bond crosses a cell face.
    The image must represent the whole connected chemical fragment: adding
    only the endpoint that crosses the face leaves, for example, a translated
    ClO4 centre with just one of its four O atoms.
    """
    if not draw_atoms or not any(atom.get("_strict_unit_cell") for atom in draw_atoms):
        return 0

    instances: dict[tuple[int, tuple[int, int, int]], int] = {}
    home_by_source: dict[int, dict[str, Any]] = {}
    for draw_index, atom in enumerate(draw_atoms):
        identity = source_image_identity(atom, source_atoms, draw_index)
        if identity is None:
            continue
        instances.setdefault(identity, draw_index)
        if identity[1] == (0, 0, 0):
            home_by_source.setdefault(identity[0], atom)

    M_arr = np.asarray(M, dtype=float)
    adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {}
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3:
            continue
        adjacency.setdefault(left, []).append((right, relation))
        adjacency.setdefault(right, []).append(
            (left, tuple(-value for value in relation))
        )

    additions: list[dict[str, Any]] = []

    def _add_image(source: int, shift: tuple[int, int, int]) -> None:
        target_key = (source, shift)
        source_atom = home_by_source.get(source)
        if source_atom is None or target_key in instances:
            return
        copied = dict(source_atom)
        wrapped_frac = np.asarray(source_atom.get("frac"), dtype=float)
        target_frac = wrapped_frac + np.asarray(shift, dtype=float)
        copied["frac"] = target_frac
        copied["cart"] = frac_to_cart(target_frac, M_arr)
        copied["_wrapped_frac"] = wrapped_frac.copy()
        copied["_image_shift"] = shift
        copied["_strict_unit_cell"] = True
        copied["_is_bonded_image_replica"] = True
        copied.pop("_is_boundary_replica", None)
        copied.pop("_is_fragment_boundary_replica", None)
        instances[target_key] = len(draw_atoms) + len(additions)
        additions.append(copied)

    # Seed image instances from canonical records whose endpoint crosses a
    # face. The checks retain the existing safety guard for each seed.
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3 or relation == (0, 0, 0):
            continue
        for source, target, shift in (
            (left, right, relation),
            (right, left, tuple(-value for value in relation)),
        ):
            source_atom = home_by_source.get(source)
            target_atom = home_by_source.get(target)
            target_key = (target, shift)
            if source_atom is None or target_atom is None or target_key in instances:
                continue
            candidate = dict(target_atom)
            candidate_frac = np.asarray(
                target_atom.get("frac"), dtype=float
            ) + np.asarray(shift, dtype=float)
            candidate["frac"] = candidate_frac
            candidate["cart"] = frac_to_cart(candidate_frac, M_arr)
            if bonds_conflict(source_atom, candidate):
                continue
            if (
                float(
                    np.linalg.norm(
                        np.asarray(candidate["cart"]) - np.asarray(source_atom["cart"])
                    )
                )
                > 3.5
            ):
                continue
            _add_image(target, shift)

    # Complete every seeded image over its canonical connected component.
    # The signed record relation maps current@q to neighbour@(q + edge).
    for (seed_source, seed_shift), _draw_index in list(instances.items()):
        if seed_shift == (0, 0, 0):
            continue
        potentials: dict[int, tuple[int, int, int]] = {seed_source: (0, 0, 0)}
        queue = [seed_source]
        while queue:
            current = queue.pop(0)
            current_potential = potentials[current]
            for neighbour, edge_shift in adjacency.get(current, ()):
                proposed = tuple(
                    current_potential[axis] + edge_shift[axis] for axis in range(3)
                )
                known = potentials.get(neighbour)
                if known is None:
                    potentials[neighbour] = proposed
                    queue.append(neighbour)
                elif known != proposed:
                    # Keep the first shortest-path potential for malformed
                    # record sets containing an inconsistent cycle.
                    continue
        for source, relative_shift in potentials.items():
            absolute_shift = tuple(
                seed_shift[axis] + relative_shift[axis] for axis in range(3)
            )
            _add_image(source, absolute_shift)

    draw_atoms.extend(additions)
    return len(additions)


def _manifest_spanning_bond_context(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    M: Any,
    canonical_bond_records: list[dict[str, Any]],
    ring_records: list[dict[str, Any]],
) -> int:
    """Materialise exact neighbouring context for periodic frameworks.

    A cell-spanning component is infinite, so whole-component replication is
    undefined and per-site face replication produces disconnected fragments.
    Signed MCK bond records identify the exact adjacent-cell endpoints.  Each
    endpoint is expanded through one complete bond shell, then any aromatic
    rings touched by that shell are completed in the same image.  This keeps
    boundary coordination centres and linkers visually symmetric without
    attempting to replicate an infinite framework component.
    """
    if not canonical_bond_records or not any(
        atom.get("_cell_spanning_component") for atom in draw_atoms
    ):
        return 0

    instances: set[tuple[int, tuple[int, int, int]]] = set()
    home_by_source: dict[int, dict[str, Any]] = {}
    for draw_index, atom in enumerate(draw_atoms):
        identity = source_image_identity(atom, source_atoms, draw_index)
        if identity is None:
            continue
        instances.add(identity)
        if identity[1] == (0, 0, 0) and atom.get("_cell_spanning_component"):
            home_by_source.setdefault(identity[0], atom)

    adjacency: dict[int, list[tuple[int, tuple[int, int, int]]]] = {}
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3:
            continue
        adjacency.setdefault(left, []).append((right, relation))
        adjacency.setdefault(right, []).append(
            (left, tuple(-value for value in relation))
        )
    for neighbours in adjacency.values():
        neighbours.sort(key=lambda item: (item[0], item[1]))

    rings_by_source: dict[int, list[set[int]]] = {}
    for ring in ring_records:
        if not bool(ring.get("is_aromatic")) or int(ring.get("size", 0)) > 8:
            continue
        members = {
            int(value)
            for value in ring.get("cycle_atom_indices", ring.get("atom_indices", ()))
        }
        members.intersection_update(home_by_source)
        if not members:
            continue
        for source in members:
            rings_by_source.setdefault(source, []).append(members)

    M_arr = np.asarray(M, dtype=float)
    additions: list[dict[str, Any]] = []

    def add_context(seed: int, shift: tuple[int, int, int]) -> None:
        placements: dict[int, tuple[int, int, int]] = {seed: (0, 0, 0)}
        for neighbour, edge_shift in adjacency.get(seed, ()):
            placements.setdefault(neighbour, edge_shift)

        ring_anchors = list(placements)
        for anchor in ring_anchors:
            for ring in rings_by_source.get(anchor, ()):
                queue = [anchor]
                visited = {anchor}
                while queue:
                    current = queue.pop(0)
                    current_shift = placements[current]
                    for neighbour, edge_shift in adjacency.get(current, ()):
                        if neighbour not in ring or neighbour in visited:
                            continue
                        visited.add(neighbour)
                        proposed = tuple(
                            current_shift[axis] + edge_shift[axis]
                            for axis in range(3)
                        )
                        placements.setdefault(neighbour, proposed)
                        queue.append(neighbour)
        for source in sorted(placements):
            relative = placements[source]
            absolute = tuple(shift[axis] + relative[axis] for axis in range(3))
            key = (source, absolute)
            home = home_by_source.get(source)
            if home is None or key in instances:
                continue
            base_frac = np.asarray(
                home.get("_wrapped_frac", source_atoms[source].get("frac")),
                dtype=float,
            )
            if base_frac.shape != (3,) or not np.all(np.isfinite(base_frac)):
                continue
            shift_arr = np.asarray(absolute, dtype=float)
            copied = dict(home)
            copied["frac"] = base_frac + shift_arr
            copied["cart"] = frac_to_cart(copied["frac"], M_arr)
            copied["_wrapped_frac"] = base_frac.copy()
            copied["_image_shift"] = absolute
            copied["_is_boundary_replica"] = True
            copied["_is_framework_context_replica"] = True
            copied.pop("_is_fragment_boundary_replica", None)
            instances.add(key)
            additions.append(copied)

    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3 or relation == (0, 0, 0):
            continue
        if left not in home_by_source or right not in home_by_source:
            continue
        add_context(right, relation)
        add_context(left, tuple(-value for value in relation))

    draw_atoms.extend(additions)
    return len(additions)


def source_image_identity(
    atom: dict[str, Any],
    source_atoms: list[dict[str, Any]],
    fallback_index: int,
) -> tuple[int, tuple[int, int, int]] | None:
    """Return a manifested atom's absolute raw-source image identity.

    ``_image_shift`` is intentionally not used here: boundary replication
    stores a shift relative to MCK's unwrapped display home image.  The stable
    identity is the integer offset between the displayed fractional coordinate
    and the source atom's crystallographically wrapped coordinate.
    """
    try:
        source_index = int(atom.get("_source_index", fallback_index))
    except (TypeError, ValueError):
        return None
    if not 0 <= source_index < len(source_atoms):
        return None
    display_frac = np.asarray(atom.get("frac"), dtype=float)
    wrapped_frac = np.asarray(
        atom.get("_wrapped_frac", source_atoms[source_index].get("frac")),
        dtype=float,
    )
    if display_frac.shape != (3,) or wrapped_frac.shape != (3,):
        return None
    delta = display_frac - wrapped_frac
    image = np.rint(delta).astype(int)
    if not np.allclose(delta, image, rtol=0.0, atol=_SOURCE_IMAGE_TOL):
        return None
    return source_index, tuple(int(value) for value in image)


def _canonical_display_bond_pairs(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    canonical_bond_records: list[dict[str, Any]],
) -> tuple[list[tuple[int, int]], dict[str, int]]:
    """Lift PBC-aware canonical records to matching display atom instances.

    For a canonical record ``(left, right, S)`` and a visible instance
    ``left@q``, the sole valid target is ``right@(q + S)``.  The lookup is
    linear in manifested atoms plus valid edge copies; it never re-perceives
    bonds or forms a Cartesian product of replica sets.
    """
    instances: dict[tuple[int, tuple[int, int, int]], int] = {}
    fragment_instances: dict[
        tuple[int, tuple[int, int, int]],
        dict[int, int],
    ] = {}
    fragment_keys_by_source: dict[
        int,
        list[tuple[int, tuple[int, int, int]]],
    ] = {}
    shifts_by_source: dict[int, list[tuple[tuple[int, int, int], int]]] = {}
    collision_keys: set[tuple[int, tuple[int, int, int]]] = set()
    unresolved_instances = 0
    for draw_index, atom in enumerate(draw_atoms):
        identity = source_image_identity(atom, source_atoms, draw_index)
        if identity is None:
            unresolved_instances += 1
            continue
        existing = instances.get(identity)
        if existing is not None:
            collision_keys.add(identity)
            continue
        instances[identity] = draw_index
        shifts_by_source.setdefault(identity[0], []).append((identity[1], draw_index))
        molecule_index = atom.get("_source_molecule_index")
        if molecule_index is not None and not atom.get("_cell_spanning_component"):
            try:
                relative_shift = tuple(
                    int(value) for value in atom.get("_image_shift", (0, 0, 0))
                )
                fragment_key = (int(molecule_index), relative_shift)
                members = fragment_instances.setdefault(fragment_key, {})
                if identity[0] not in members:
                    fragment_keys_by_source.setdefault(identity[0], []).append(
                        fragment_key
                    )
                members[identity[0]] = draw_index
            except (TypeError, ValueError):
                pass

    pairs: set[tuple[int, int]] = set()
    missing_targets = 0
    instance_lookups = 0
    max_copies_per_record = 0
    for record in canonical_bond_records:
        try:
            left = int(record["left"])
            right = int(record["right"])
            relation = tuple(int(value) for value in record["right_image_shift"])
        except (KeyError, TypeError, ValueError):
            continue
        if len(relation) != 3:
            continue
        emitted_for_record: set[tuple[int, int]] = set()
        for fragment_key in sorted(fragment_keys_by_source.get(left, ())):
            members = fragment_instances[fragment_key]
            if left in members and right in members:
                pair = (members[left], members[right])
            else:
                continue
            instance_lookups += 1
            pair_key = tuple(sorted(pair))
            pairs.add(pair_key)
            emitted_for_record.add(pair_key)
        directions = (
            (left, right, relation),
            (right, left, tuple(-value for value in relation)),
        )
        for source, target, shift in directions:
            for image, source_draw_index in shifts_by_source.get(source, ()):
                source_key = (source, image)
                if source_key in collision_keys:
                    continue
                target_image = tuple(image[axis] + shift[axis] for axis in range(3))
                target_key = (target, target_image)
                instance_lookups += 1
                target_draw_index = instances.get(target_key)
                if target_draw_index is None or target_key in collision_keys:
                    missing_targets += 1
                    continue
                pair_key = tuple(sorted((source_draw_index, target_draw_index)))
                pairs.add(pair_key)
                emitted_for_record.add(pair_key)
            max_copies_per_record = max(max_copies_per_record, len(emitted_for_record))

    return sorted(pairs), {
        "display_instances": len(instances),
        "instance_collisions": len(collision_keys),
        "unresolved_instances": unresolved_instances,
        "instance_lookups": instance_lookups,
        "missing_target_instances": missing_targets,
        "max_copies_per_record": max_copies_per_record,
    }


def _prune_unconnected_spanning_replicas(
    draw_atoms: list[dict[str, Any]],
    source_atoms: list[dict[str, Any]],
    canonical_bond_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not canonical_bond_records:
        return draw_atoms
    pairs, _stats = _canonical_display_bond_pairs(
        draw_atoms,
        source_atoms,
        canonical_bond_records,
    )
    connected = {index for pair in pairs for index in pair}
    bonded_sources = {
        int(record[endpoint])
        for record in canonical_bond_records
        for endpoint in ("left", "right")
    }
    return [
        atom
        for index, atom in enumerate(draw_atoms)
        if not (
            atom.get("_cell_spanning_component")
            and atom.get("_is_boundary_replica")
            and int(atom.get("_source_index", -1)) in bonded_sources
            and index not in connected
        )
    ]


def build_scene_from_atoms(
    *,
    name: str,
    title: str,
    atoms,
    cell,
    M,
    R,
    show_hydrogen: bool = False,
    preset: Optional[Dict[str, Any]] = None,
    display_mode: str = "formula_unit",
    ops=None,
    formula_unit_atoms=None,
    unwrapped_atoms=None,
    include_boundary_replicas: bool = True,
    bond_scale: float | None = None,
    bond_thresholds: dict[tuple[str, str], float] | None = None,
    canonical_bond_pairs: list[tuple[int, int]] | None = None,
    canonical_bond_records: list[dict[str, Any]] | None = None,
    molcrys_analysis=None,
) -> Dict[str, Any]:
    ops = scene_ops() if ops is None else ops
    preset = default_preset() if preset is None else preset
    style = deep_merge(DEFAULT_STYLE, preset.get("style"))
    entry = preset.get("structures", {}).get(name, {})
    style = deep_merge(style, entry.get("style"))
    show_h = bool(show_hydrogen) or bool(
        entry.get("show_hydrogen", style.get("show_hydrogen", False))
    )

    input_atoms = [dict(atom) for atom in atoms]
    if molcrys_analysis is None:
        if canonical_bond_pairs is not None or canonical_bond_records is not None:
            raise TypeError(
                "canonical bond projections require the MolCrysKit analysis that "
                "provided their SiteRecord identities; pass molcrys_analysis."
            )
        analyze_kwargs: dict[str, Any] = {}
        if bond_scale is not None:
            analyze_kwargs["bond_scale"] = bond_scale
        if bond_thresholds is not None:
            analyze_kwargs["bond_thresholds"] = bond_thresholds
        molcrys_analysis = molcrys_bridge.analyze(input_atoms, M, **analyze_kwargs)
    molcrys_bridge.require_structure_contract(
        molcrys_analysis,
        atom_count=len(input_atoms),
        require_formula_unit=display_mode == "formula_unit",
    )
    source_atoms = molcrys_bridge.atoms_with_site_provenance(
        input_atoms,
        molcrys_analysis,
    )
    canonical_unwrapped_atoms = None
    if unwrapped_atoms is not None:
        if len(unwrapped_atoms) != len(source_atoms):
            raise ValueError(
                "unwrapped_atoms must preserve the MolCrysKit SiteRecord order."
            )
        canonical_unwrapped_atoms = []
        provenance_keys = (
            "_source_index",
            "_source_molecule_index",
            "_molecule_index",
            "_molecule_local_index",
            "_wrapped_frac",
            "_mck_image_shift",
        )
        for unwrapped, source in zip(unwrapped_atoms, source_atoms):
            copied = dict(unwrapped)
            for key in provenance_keys:
                if key in source:
                    copied.setdefault(key, copy.deepcopy(source[key]))
            canonical_unwrapped_atoms.append(copied)
    canonical_records = [dict(record) for record in molcrys_analysis.bond_records]
    canonical_pairs = sorted(
        {
            tuple(sorted((int(record["left"]), int(record["right"]))))
            for record in canonical_records
        }
    )
    if canonical_bond_records is not None:
        supplied = {
            (
                int(record["left"]),
                int(record["right"]),
                tuple(int(value) for value in record["right_image_shift"]),
            )
            for record in canonical_bond_records
        }
        canonical = {
            (
                int(record["left"]),
                int(record["right"]),
                tuple(int(value) for value in record["right_image_shift"]),
            )
            for record in canonical_records
        }
        if supplied != canonical:
            raise ValueError(
                "canonical_bond_records disagrees with molcrys_analysis; "
                "MatterVis will not replace MolCrysKit connectivity."
            )
    if canonical_bond_pairs is not None and {
        tuple(sorted((int(left), int(right)))) for left, right in canonical_bond_pairs
    } != set(canonical_pairs):
        raise ValueError(
            "canonical_bond_pairs disagrees with molcrys_analysis; MatterVis "
            "will not replace MolCrysKit connectivity."
        )

    if display_mode == "formula_unit":
        canonical_formula_atoms = molcrys_bridge.select_formula_unit(
            source_atoms,
            M,
            analysis=molcrys_analysis,
        )
        if formula_unit_atoms is not None:

            def _formula_signature(items):
                return [
                    (
                        int(atom.get("_source_index", index)),
                        tuple(np.round(np.asarray(atom["cart"], dtype=float), 7)),
                    )
                    for index, atom in enumerate(items)
                ]

            if _formula_signature(formula_unit_atoms) != _formula_signature(
                canonical_formula_atoms
            ):
                raise ValueError(
                    "formula_unit_atoms disagrees with MolCrysKit's "
                    "FormulaUnitSelection."
                )
        formula_unit_atoms = canonical_formula_atoms

    sel_atoms = selected_atoms_for_mode(
        ops,
        source_atoms,
        M,
        cell,
        display_mode=display_mode,
        formula_unit_atoms=formula_unit_atoms,
        unwrapped_atoms=canonical_unwrapped_atoms,
        include_boundary_replicas=include_boundary_replicas,
    )
    draw_atoms = [dict(atom) for atom in sel_atoms if show_h or atom["elem"] != "H"]

    image_records = list(canonical_records)
    strict_cell = display_mode == "unit_cell" and not include_boundary_replicas
    bonded_image_replica_count = 0
    framework_context_replica_count = 0
    if strict_cell and image_records:
        bonded_image_replica_count = _manifest_strict_bonded_images(
            draw_atoms,
            source_atoms,
            M,
            image_records,
        )
    elif display_mode == "unit_cell" and include_boundary_replicas:
        framework_context_replica_count = _manifest_spanning_bond_context(
            draw_atoms,
            source_atoms,
            M,
            image_records,
            list(getattr(molcrys_analysis, "ring_records", ()) or ()),
        )
    draw_atoms = _prune_unconnected_spanning_replicas(
        draw_atoms,
        source_atoms,
        image_records,
    )

    view_x = np.array(R[0], dtype=float)
    view_y = np.array(R[1], dtype=float)
    view_z = np.array(R[2], dtype=float)

    if draw_atoms:
        depths = np.array([atom["cart"] @ view_z for atom in draw_atoms], dtype=float)
        z_min, z_max = depths.min(), depths.max()
        z_span = max(z_max - z_min, 1e-6)
        for atom, depth in zip(draw_atoms, depths):
            atom["_depth_t"] = float((depth - z_min) / z_span)
            atom["is_minor"] = atom_is_minor(atom)
            atom["is_disordered"] = atom_is_disordered(atom)
            atom["disorder_alpha"] = float(ops.disorder_alpha(atom))
            atom["color"] = ops.elem_color(atom["elem"])
            atom["color_light"] = ops.elem_color_light(atom["elem"])
            atom["atom_radius"] = float(ops.atom_r(atom["elem"]))

    bond_source = "canonical_mck"

    telemetry_enabled = os.environ.get("MATTERVIS_SCENE_PERF_EVENTS") == "1"
    bond_started = time.perf_counter() if telemetry_enabled else None
    canonical_stats: dict[str, int] = {}
    bond_pairs, canonical_stats = _canonical_display_bond_pairs(
        draw_atoms,
        source_atoms,
        canonical_records,
    )
    bonds = []
    conflict_drops = 0
    render_length_drops = 0
    for i, j in bond_pairs:
        ai = draw_atoms[i]
        aj = draw_atoms[j]
        if bonds_conflict(ai, aj):
            if telemetry_enabled:
                conflict_drops += 1
            continue
        start, end = _bond_endpoints(ai, aj, cell, display_mode=display_mode)
        # Skip bonds whose rendered length far exceeds a covalent bond —
        # these are cross-cell PBC bonds where one atom sits near one face
        # and the other near the opposite face. The bond is real but its
        # visual representation would span the entire cell as a long line.
        rendered_len = float(np.linalg.norm(end - start))
        if rendered_len > 3.5:
            if telemetry_enabled:
                render_length_drops += 1
            continue
        bonds.append(
            {
                "i": i,
                "j": j,
                "start": start.copy(),
                "end": end.copy(),
                "color_i": ai["color"],
                "color_j": aj["color"],
                "alpha_i": ai["disorder_alpha"],
                "alpha_j": aj["disorder_alpha"],
                "is_minor": bond_is_minor(ai, aj),
                "is_disordered": bond_is_disordered(ai, aj),
                "occ": min(float(ai.get("occ", 1.0)), float(aj.get("occ", 1.0))),
                "depth_t": float((ai["_depth_t"] + aj["_depth_t"]) / 2.0),
            }
        )
    if os.environ.get("MATTERVIS_SCENE_PERF_EVENTS") == "1":
        perf_log.record(
            "scene:bonds",
            kind="event",
            info={
                "source": bond_source,
                "mapping": "source_image_lift",
                "display_mode": display_mode,
                "canonical_records": len(canonical_records or []),
                "draw_atoms": len(draw_atoms),
                "input_pairs": len(bond_pairs),
                "rendered_bonds": len(bonds),
                "conflict_drops": conflict_drops,
                "render_length_drops": render_length_drops,
                "duration_ms": (time.perf_counter() - bond_started) * 1000.0,
                **canonical_stats,
            },
        )

    label_items = legacy_scene._label_payload(ops, draw_atoms, view_x, view_y, view_z)
    bounds = legacy_scene._compute_bounds(
        draw_atoms or sel_atoms,
        view_x,
        view_y,
        view_z,
        atom_scale=float(style.get("atom_scale", 1.0)),
    )
    camera = entry.get("camera") or legacy_scene._camera_from_bounds(
        bounds, view_y, view_z
    )

    M_arr = np.asarray(M, dtype=float)
    projected_axes = [
        (float(M_arr[i] @ view_x), float(M_arr[i] @ view_y)) for i in range(3)
    ]
    axis_labels = list(style.get("axes_labels") or ["a", "b", "c"])[:3]

    scene = {
        "name": name,
        "title": title,
        "cell": cell,
        "M": M,
        "R": np.array(R, dtype=float),
        "view_x": view_x,
        "view_y": view_y,
        "view_z": view_z,
        "selected_atoms": sel_atoms,
        "draw_atoms": draw_atoms,
        "bonds": bonds,
        "label_items": label_items,
        "bounds": bounds,
        "camera": camera,
        "style": style,
        "show_hydrogen": show_h,
        "has_minor": any(bool(atom["is_minor"]) for atom in draw_atoms),
        "preset_entry": entry,
        "display_mode": display_mode,
        "unit_cell_boundary_replicas": bool(include_boundary_replicas),
        "bonded_image_replica_count": bonded_image_replica_count,
        "framework_context_replica_count": framework_context_replica_count,
        "bond_scale": bond_scale,
        "bond_thresholds": copy.deepcopy(bond_thresholds),
        "projected_axes": projected_axes,
        "axis_labels": axis_labels,
        "_canonical_source_atoms": [dict(atom) for atom in source_atoms],
        "_canonical_site_records": [
            {
                "global_index": int(record.global_index),
                "molecule_index": int(record.molecule_index),
                "local_index": int(record.local_index),
                "asym_index": record.asym_index,
                "sym_op_index": record.sym_op_index,
                "image_shift": [int(value) for value in record.image_shift],
            }
            for record in molcrys_analysis.site_records
        ],
        "_canonical_bond_records": copy.deepcopy(canonical_records),
    }
    apply_element_colors(
        scene,
        style.get("element_colors"),
        style.get("element_colors_light"),
    )
    return scene


def build_scene_from_cif(
    *,
    name: str,
    cif_path: str,
    title: str,
    preset: Optional[Dict[str, Any]] = None,
    show_hydrogen: bool = False,
    display_mode: str = "formula_unit",
    ops=None,
) -> Dict[str, Any]:
    ops = scene_ops() if ops is None else ops
    preset = default_preset() if preset is None else preset
    parsed = load_cif(cif_path)
    atoms = [dict(atom) for atom in parsed.atoms]
    cell = parsed.cell
    M = np.asarray(parsed.matrix, dtype=float)
    analysis = molcrys_bridge.analyze_crystal(parsed.crystal)
    view_dir, up = legacy_scene._resolve_view(
        ops,
        name,
        atoms,
        M,
        cell,
        preset,
        molcrys_analysis=analysis,
    )
    R = ops.view_rotation(view_dir, up)
    scene = build_scene_from_atoms(
        name=name,
        title=title,
        atoms=atoms,
        cell=cell,
        M=M,
        R=R,
        preset=preset,
        show_hydrogen=show_hydrogen,
        display_mode=display_mode,
        ops=ops,
        unwrapped_atoms=None,
        molcrys_analysis=analysis,
    )
    scene["cif_path"] = cif_path
    scene["view_direction"] = np.array(view_dir, dtype=float)
    scene["up"] = np.array(up, dtype=float)
    return scene
