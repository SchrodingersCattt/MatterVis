"""Load cube files through the standard crystal structure pipeline.

Produces a :class:`~crystal_viewer.loader.core.LoadedCrystal` bundle where
the atomic structure is processed identically to a CIF upload (MCK analysis,
fragment detection, display modes) and the volumetric data is attached for
optional isosurface overlay rendering.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from .. import perf_log
from ..cube import CubeData, read_cube
from ..cube.bridge import cube_lattice_matrix, cube_to_cell, cube_to_raw_atoms
from ..scene import build_scene_from_atoms, scene_ops
from ..structure import molcrys_bridge
from .core import (
    LoadedCrystal,
    _fragment_table_from_atoms,
    _unwrapped_atoms_from_atoms,
    _UPLOAD_DEFAULT_VIEW_DIR,
    _UPLOAD_DEFAULT_UP,
)


def build_loaded_crystal_from_cube(
    cube: CubeData,
    *,
    name: Optional[str] = None,
    title: Optional[str] = None,
    preset: Optional[Dict[str, Any]] = None,
    source: str = "cube",
    bond_scale: float = 1.0,
    bond_thresholds: Optional[Dict[tuple[str, str], float]] = None,
) -> LoadedCrystal:
    """Build a full LoadedCrystal bundle from a CubeData object.

    The atoms go through the standard MCK analysis and scene pipeline,
    giving proper bond perception, fragment detection, and display modes.
    The volumetric data (``cube``) is stored on the returned bundle as
    ``bundle.cube_data`` for downstream isosurface rendering.
    """
    if name is None:
        name = cube.path.stem
    if title is None:
        title = name

    ops = scene_ops()
    preset = preset or {}

    # Phase 1: derive pipeline inputs from cube
    with perf_log.time_block("cube_loader:convert", kind="event", structure=name):
        raw_atoms = cube_to_raw_atoms(cube)
        cell = cube_to_cell(cube)
        M = cube_lattice_matrix(cube)

    # Phase 2: MCK analysis (bonds, molecules, species)
    n_atoms = len(raw_atoms)
    with perf_log.time_block("cube_loader:molcrys_analyze", kind="event", structure=name, n_atoms=n_atoms):
        molcrys_analysis = molcrys_bridge.analyze(
            raw_atoms,
            M,
            bond_scale=bond_scale,
            bond_thresholds=bond_thresholds,
        )

    # Phase 3: formula unit selection
    with perf_log.time_block("cube_loader:select_formula_unit", kind="event", structure=name):
        formula_unit_atoms = molcrys_bridge.select_formula_unit(raw_atoms, M, analysis=molcrys_analysis)

    # Phase 4: PBC unwrapping
    with perf_log.time_block("cube_loader:unwrap_atoms", kind="event", structure=name):
        unwrapped_atoms, unwrap_overflow = _unwrapped_atoms_from_atoms(
            raw_atoms, cell, M,
            include_minor=True,
            molcrys_analysis=molcrys_analysis,
        )

    # Phase 5: view direction — use upload-style diagonal default
    view_dir = _UPLOAD_DEFAULT_VIEW_DIR.copy()
    up = _UPLOAD_DEFAULT_UP.copy()
    R = ops.view_rotation(view_dir, up)

    # Phase 6: build scene
    with perf_log.time_block("cube_loader:build_scene", kind="event", structure=name, n_atoms=n_atoms):
        initial_scene = build_scene_from_atoms(
            name=name,
            title=title,
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
            canonical_bond_pairs=molcrys_analysis.bond_pairs,
            canonical_bond_records=molcrys_analysis.bond_records,
        )
    initial_scene["cif_path"] = str(cube.path)
    initial_scene["view_direction"] = np.array(view_dir, dtype=float)
    initial_scene["up"] = np.array(up, dtype=float)
    initial_scene["unwrap_overflow"] = copy.deepcopy(unwrap_overflow)
    # Attach cube_data reference so render pipeline can generate isosurfaces
    initial_scene["cube_data"] = cube

    # Phase 7: fragment tables
    with perf_log.time_block("cube_loader:fragment_table", kind="event", structure=name):
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

    with perf_log.time_block("cube_loader:fragment_table_topology", kind="event", structure=name):
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
        title=title,
        cif_path=str(cube.path),
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
        bond_scale=bond_scale,
        bond_thresholds=copy.deepcopy(bond_thresholds),
    )
    # Store cube data on the bundle for isosurface generation
    bundle.cube_data = cube  # type: ignore[attr-defined]
    return bundle


def load_cube_file(
    path: str | Path,
    *,
    name: Optional[str] = None,
    preset: Optional[Dict[str, Any]] = None,
    source: str = "cube",
    bond_scale: float = 1.0,
    bond_thresholds: Optional[Dict[tuple[str, str], float]] = None,
) -> LoadedCrystal:
    """Convenience: read a .cube file and return a full LoadedCrystal bundle."""
    cube = read_cube(path)
    return build_loaded_crystal_from_cube(
        cube,
        name=name,
        preset=preset,
        source=source,
        bond_scale=bond_scale,
        bond_thresholds=bond_thresholds,
    )
