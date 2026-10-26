from __future__ import annotations

import copy
import os
import time
from types import SimpleNamespace
from typing import Any, Dict, Optional

import numpy as np
from molcrys_kit.utils.geometry import frac_to_cart  # noqa: F401

from .. import perf_log
from ..structure.bonds import (
    bonds_conflict,
    inorganic_solid_profile,
)
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

from .periodic import (
    _canonical_display_bond_pairs,
    _manifest_spanning_bond_context,
    _manifest_strict_bonded_images,
    _periodic_component_ranks,
    _prune_unconnected_spanning_replicas,
    manifested_bond_endpoints,
    rendered_bond_keeps_record,
    scene_ops,
    source_image_identity,
)



def build_scene_from_atoms(
    *,
    name: str,
    title: str,
    atoms,
    cell,
    M,
    R,
    show_hydrogen: bool | None = None,
    preset: Optional[Dict[str, Any]] = None,
    display_mode: str = "formula_unit",
    ops=None,
    formula_unit_atoms=None,
    unwrapped_atoms=None,
    include_boundary_replicas: bool = True,
    include_cross_boundary_bond_endpoints: bool = True,
    include_minor: bool = True,
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
    show_h = (
        bool(entry.get("show_hydrogen", style.get("show_hydrogen", False)))
        if show_hydrogen is None
        else bool(show_hydrogen)
    )
    style["show_hydrogen"] = show_h

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
            "_source_site_id",
            "_disorder_token",
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
    inorganic_profile = inorganic_solid_profile(source_atoms)
    # Lattice versus molecule is MolCrysKit's connectivity regime (rank of
    # the signed bond graph, including coordination edges). Element
    # whitelists do not choose the display. Scenes without a report keep
    # the inorganic-composition fallback used before that API existed.
    connectivity_regime = getattr(molcrys_analysis, "connectivity_regime", None)
    connectivity_rank = getattr(molcrys_analysis, "connectivity_rank", None)
    periodic_ranks = _periodic_component_ranks(
        len(source_atoms),
        canonical_records,
    )
    for source_index, rank in enumerate(periodic_ranks):
        source_atoms[source_index]["_periodic_component_rank"] = rank
        if canonical_unwrapped_atoms is not None:
            canonical_unwrapped_atoms[source_index]["_periodic_component_rank"] = rank
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

    # An extended connectivity regime is a lattice, not a molecule. A
    # formula-unit unwrap and a spanning-bond shell both park framework
    # sites at fractional coordinates such as -0.5, which draws a
    # one-sided halo outside the conventional cell. Wrapped
    # crystallographic sites stay inside [0, 1].
    if connectivity_regime == "extended":
        lattice_site_display = display_mode in {"formula_unit", "unit_cell"}
    elif connectivity_regime == "molecular":
        lattice_site_display = False
    else:
        lattice_site_display = display_mode in {"formula_unit", "unit_cell"} and bool(
            inorganic_profile.get("is_inorganic_solid")
        )
    sel_atoms = selected_atoms_for_mode(
        ops,
        source_atoms,
        M,
        cell,
        display_mode=display_mode,
        formula_unit_atoms=formula_unit_atoms,
        unwrapped_atoms=canonical_unwrapped_atoms,
        include_boundary_replicas=include_boundary_replicas,
        lattice_site_display=lattice_site_display,
    )
    draw_atoms = [dict(atom) for atom in sel_atoms if show_h or atom["elem"] != "H"]
    if not include_minor:
        draw_atoms = [atom for atom in draw_atoms if not ops.is_minor(atom)]

    image_records = list(canonical_records)
    strict_cell = display_mode == "unit_cell" and not include_boundary_replicas
    bonded_image_replica_count = 0
    framework_context_replica_count = 0
    # Lattice sites already carry their symmetric boundary images. Adding
    # signed coordination endpoints here rebuilds the exterior halo
    # (strict-cell NaCl grows to the full neighbour shell outside [0, 1]).
    if (
        not lattice_site_display
        and strict_cell
        and image_records
        and include_cross_boundary_bond_endpoints
    ):
        bonded_image_replica_count = _manifest_strict_bonded_images(
            draw_atoms,
            source_atoms,
            M,
            image_records,
        )
    elif (
        not lattice_site_display
        and display_mode == "unit_cell"
        and include_boundary_replicas
    ):
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
            atom["disorder_resolved"] = bool(
                atom.get("disorder_resolved", "_is_minor" in atom)
            )
            atom["disorder_alpha"] = float(ops.disorder_alpha(atom))
            atom["color"] = ops.elem_color(atom["elem"])
            atom["color_light"] = ops.elem_color_light(atom["elem"])
            atom["atom_radius"] = float(ops.atom_r(atom["elem"]))

    bond_source = "canonical_mck"

    telemetry_enabled = os.environ.get("MATTERVIS_SCENE_PERF_EVENTS") == "1"
    bond_started = time.perf_counter() if telemetry_enabled else None
    canonical_stats: dict[str, int] = {}
    bond_pairs, canonical_stats, pair_metadata = _canonical_display_bond_pairs(
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
        meta = pair_metadata.get((i, j), {})
        role = str(meta.get("role") or "covalent")
        record_distance = meta.get("distance")
        # Covalent and coordination edges are both stick-eligible. The
        # role is MolCrysKit's; ball and space-filling renderers simply
        # do not stroke the scene bond list. Endpoints stay on the
        # manifested image whose length matches the record, so a
        # half-cell contact is not flipped outside the cell.
        start, end = manifested_bond_endpoints(
            ai,
            aj,
            cell,
            display_mode,
            record_distance,
        )
        if not rendered_bond_keeps_record(start, end, record_distance):
            if telemetry_enabled:
                render_length_drops += 1
            continue
        bonds.append(
            {
                "i": i,
                "j": j,
                "start": start.copy(),
                "end": end.copy(),
                "role": role,
                "distance": record_distance,
                "color_i": ai["color"],
                "color_j": aj["color"],
                "alpha_i": ai["disorder_alpha"],
                "alpha_j": aj["disorder_alpha"],
                "is_minor": bond_is_minor(ai, aj),
                "is_disordered": bond_is_disordered(ai, aj),
                "disorder_resolved": bool(
                    ai.get("disorder_resolved", False)
                    and aj.get("disorder_resolved", False)
                ),
                "occ": min(float(ai.get("occ", 1.0)), float(aj.get("occ", 1.0))),
                "depth_t": float((ai["_depth_t"] + aj["_depth_t"]) / 2.0),
            }
        )
    coordination_sites = {
        index
        for bond in bonds
        if bond.get("role") == "coordination"
        for index in (int(bond["i"]), int(bond["j"]))
    }
    for index, atom in enumerate(draw_atoms):
        atom["_coordination_site"] = index in coordination_sites
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
        "connectivity_regime": connectivity_regime,
        "connectivity_rank": connectivity_rank,
        **inorganic_profile,
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
    show_hydrogen: bool | None = None,
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
