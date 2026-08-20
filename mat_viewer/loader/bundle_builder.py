"""Build canonical LoadedCrystal bundles from parsed atoms and a lattice."""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

import numpy as np

from .. import perf_log
from ..scene import build_scene_from_atoms, legacy_scene, scene_ops
from ..structure import molcrys_bridge
from .core import (
    LoadedCrystal,
    _fragment_table_from_atoms,
    _unwrapped_atoms_from_atoms,
    _upload_default_view,
)


def build_loaded_crystal_from_atoms(
    *,
    name: str,
    source_path: str,
    raw_atoms,
    cell,
    M,
    title: Optional[str] = None,
    preset: Optional[Dict[str, Any]] = None,
    source: str = "upload",
    view_weights: Optional[Dict[str, float]] = None,
    molcrys_analysis=None,
    bond_scale: float | None = None,
    bond_thresholds: dict[tuple[str, str], float] | None = None,
    scene_metadata_extra: Optional[Dict[str, Any]] = None,
) -> LoadedCrystal:
    """Build the canonical render bundle from parsed atoms and a lattice.

    File-format adapters own parsing only. CIF, Cube, ASE, VASP and trajectory
    readers all converge here before scene construction and rendering.
    """
    ops = scene_ops()
    preset = preset or {}
    M = np.asarray(M, dtype=float)
    raw_atoms = [dict(atom) for atom in raw_atoms]
    n_atoms = len(raw_atoms)

    if molcrys_analysis is None:
        with perf_log.time_block(
            "loader:molcrys_analyze",
            kind="event",
            structure=name,
            n_atoms=n_atoms,
        ):
            analyze_kwargs = {}
            if bond_scale is not None:
                analyze_kwargs["bond_scale"] = bond_scale
            if bond_thresholds is not None:
                analyze_kwargs["bond_thresholds"] = bond_thresholds
            molcrys_analysis = molcrys_bridge.analyze(
                raw_atoms,
                M,
                **analyze_kwargs,
            )

    with perf_log.time_block(
        "loader:select_formula_unit", kind="event", structure=name
    ):
        formula_unit_atoms = molcrys_bridge.select_formula_unit(
            raw_atoms,
            M,
            analysis=molcrys_analysis,
        )
    with perf_log.time_block("loader:unwrap_atoms", kind="event", structure=name):
        unwrapped_atoms, unwrap_overflow = _unwrapped_atoms_from_atoms(
            raw_atoms,
            cell,
            M,
            include_minor=True,
            molcrys_analysis=molcrys_analysis,
        )

    if source == "catalog":
        with perf_log.time_block("loader:resolve_view", kind="event", structure=name):
            legacy_M = M.T
            view_dir, up = legacy_scene._resolve_view(
                ops,
                name,
                raw_atoms,
                legacy_M,
                cell,
                preset,
                view_weights=view_weights,
            )
    else:
        with perf_log.time_block(
            "loader:default_view",
            kind="event",
            structure=name,
            reason="skip_auto_view_for_external_input",
        ):
            view_dir, up = _upload_default_view(name, preset)

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
            bond_scale=bond_scale,
            bond_thresholds=bond_thresholds,
            canonical_bond_pairs=getattr(molcrys_analysis, "bond_pairs", None),
            canonical_bond_records=getattr(molcrys_analysis, "bond_records", None),
        )
    initial_scene["cif_path"] = source_path
    initial_scene["source_path"] = source_path
    initial_scene["view_direction"] = np.asarray(view_dir, dtype=float)
    initial_scene["up"] = np.asarray(up, dtype=float)
    initial_scene["unwrap_overflow"] = copy.deepcopy(unwrap_overflow)
    initial_scene["rings"] = copy.deepcopy(
        getattr(molcrys_analysis, "ring_records", ())
    )
    if scene_metadata_extra:
        initial_scene.update(copy.deepcopy(scene_metadata_extra))

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
        cif_path=source_path,
        scene=initial_scene,
        raw_atoms=raw_atoms,
        cell=cell,
        M=M,
        view_direction=np.asarray(view_dir, dtype=float).tolist(),
        up=np.asarray(up, dtype=float).tolist(),
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
        bond_scale=bond_scale,
        bond_thresholds=copy.deepcopy(bond_thresholds),
    )
    return bundle
