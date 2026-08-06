from __future__ import annotations

import copy
import os
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart  # noqa: F401

from .. import perf_log
from ..structure.bonds import bonds_conflict, find_bonds
from ..structure.cif_parse import parse_asu
from ..style.disorder import (
    atom_is_disordered,
    atom_is_minor,
    bond_is_disordered,
    bond_is_minor,
    disorder_alpha,
    is_minor,
)
from ..structure.formula_unit import cluster_atoms, select_formula_unit  # noqa: F401
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
    "legacy_scene",
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
        find_bonds=find_bonds,
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
    if display_mode in ("formula_unit", "cluster") or (ai.get("_unwrapped") and aj.get("_unwrapped")):
        end = np.array(aj["cart"], dtype=float)
    else:
        end = np.array(_nearest_pbc_cart(ai["cart"], aj["cart"], cell), dtype=float)
    return start, end


_SOURCE_IMAGE_TOL = 1e-5


def _source_image_identity(
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
        identity = _source_image_identity(atom, source_atoms, draw_index)
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
        if molecule_index is not None:
            try:
                relative_shift = tuple(int(value) for value in atom.get("_image_shift", (0, 0, 0)))
                fragment_key = (int(molecule_index), relative_shift)
                members = fragment_instances.setdefault(fragment_key, {})
                if identity[0] not in members:
                    fragment_keys_by_source.setdefault(identity[0], []).append(fragment_key)
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


def _canonical_display_pair_instances(
    draw_atoms: list[dict[str, Any]],
    canonical_bond_pairs: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Lift legacy source-index pairs to every matching fragment image.

    Older MolCrysKit releases expose ``bond_pairs`` without signed PBC
    ``bond_records``. A one-value ``source_index -> draw_index`` mapping drops
    all but one boundary-fragment instance. Group by source molecule and
    display image instead, then emit an edge in every group containing both
    endpoints. Ungrouped atoms retain the historical one-instance fallback.
    """
    fragment_members: dict[tuple[int, tuple[int, int, int]], dict[int, int]] = {}
    fragment_keys_by_source: dict[int, set[tuple[int, tuple[int, int, int]]]] = {}
    ungrouped: dict[int, int] = {}
    for draw_index, atom in enumerate(draw_atoms):
        try:
            source_index = int(atom.get("_source_index", draw_index))
        except (TypeError, ValueError):
            continue
        molecule_index = atom.get("_source_molecule_index")
        if molecule_index is None:
            ungrouped.setdefault(source_index, draw_index)
            continue
        try:
            image_shift = tuple(int(value) for value in atom.get("_image_shift", (0, 0, 0)))
            fragment_key = (int(molecule_index), image_shift)
        except (TypeError, ValueError):
            ungrouped.setdefault(source_index, draw_index)
            continue
        fragment_members.setdefault(fragment_key, {}).setdefault(source_index, draw_index)
        fragment_keys_by_source.setdefault(source_index, set()).add(fragment_key)

    pairs: set[tuple[int, int]] = set()
    for raw_left, raw_right in canonical_bond_pairs:
        left, right = int(raw_left), int(raw_right)
        common_keys = fragment_keys_by_source.get(left, set()) & fragment_keys_by_source.get(right, set())
        for key in common_keys:
            members = fragment_members[key]
            pairs.add(tuple(sorted((members[left], members[right]))))
        if not common_keys and left in ungrouped and right in ungrouped:
            pairs.add(tuple(sorted((ungrouped[left], ungrouped[right]))))
    return sorted(pairs)


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
) -> Dict[str, Any]:
    ops = scene_ops() if ops is None else ops
    preset = default_preset() if preset is None else preset
    style = deep_merge(DEFAULT_STYLE, preset.get("style"))
    entry = preset.get("structures", {}).get(name, {})
    style = deep_merge(style, entry.get("style"))
    show_h = bool(show_hydrogen) or bool(entry.get("show_hydrogen", style.get("show_hydrogen", False)))

    sel_atoms = selected_atoms_for_mode(
        ops,
        atoms,
        M,
        cell,
        display_mode=display_mode,
        formula_unit_atoms=formula_unit_atoms,
        unwrapped_atoms=unwrapped_atoms,
        include_boundary_replicas=include_boundary_replicas,
    )
    draw_atoms = [dict(atom) for atom in sel_atoms if show_h or atom["elem"] != "H"]

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

    effective_cell = None if display_mode == "cluster" else cell
    # Pass M so that the KDTree pre-filter generates ghost images for
    # cross-cell bonds in framework structures (atoms that were NOT
    # unwrapped retain their wrapped cart positions and may have bonded
    # neighbours on the other side of the cell boundary).  Without M
    # the KDTree uses non-PBC Cartesian distances and misses these pairs.
    effective_M = None if display_mode == "cluster" else M
    canonical_pairs = None
    canonical_records = None
    if display_mode in ("formula_unit", "unit_cell"):
        canonical_pairs = canonical_bond_pairs
        canonical_records = canonical_bond_records
    bond_source = "canonical_mck" if canonical_pairs is not None else "redetect"
    telemetry_enabled = os.environ.get("MATTERVIS_SCENE_PERF_EVENTS") == "1"
    bond_started = time.perf_counter() if telemetry_enabled else None
    canonical_stats: dict[str, int] = {}
    if canonical_records:
        bond_pairs, canonical_stats = _canonical_display_bond_pairs(
            draw_atoms,
            atoms,
            canonical_records,
        )
    elif canonical_pairs is not None:
        bond_pairs = _canonical_display_pair_instances(draw_atoms, canonical_pairs)
    elif bond_scale is None and bond_thresholds is None:
        bond_pairs = ops.find_bonds(
            draw_atoms,
            M=effective_M,
            cell=effective_cell,
        )
    else:
        bond_pairs = ops.find_bonds(
            draw_atoms,
            M=effective_M,
            cell=effective_cell,
            bond_scale=bond_scale,
            bond_thresholds=bond_thresholds,
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
                "mapping": "source_image_lift" if canonical_records else "legacy_source_map",
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
    camera = entry.get("camera") or legacy_scene._camera_from_bounds(bounds, view_y, view_z)

    M_arr = np.asarray(M, dtype=float)
    projected_axes = [
        (float(M_arr[i] @ view_x), float(M_arr[i] @ view_y))
        for i in range(3)
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
        "bond_scale": bond_scale,
        "bond_thresholds": copy.deepcopy(bond_thresholds),
        "projected_axes": projected_axes,
        "axis_labels": axis_labels,
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
    atoms, cell, legacy_M = ops.parse_asu(cif_path)
    M = np.asarray(legacy_M, dtype=float).T
    view_dir, up = legacy_scene._resolve_view(ops, name, atoms, legacy_M, cell, preset)
    R = ops.view_rotation(view_dir, up)
    formula_unit_atoms = None
    if display_mode == "formula_unit":
        from ..structure import molcrys_bridge
        formula_unit_atoms = molcrys_bridge.select_formula_unit(atoms, M)
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
        formula_unit_atoms=formula_unit_atoms,
        unwrapped_atoms=None,
    )
    scene["cif_path"] = cif_path
    scene["view_direction"] = np.array(view_dir, dtype=float)
    scene["up"] = np.array(up, dtype=float)
    return scene
