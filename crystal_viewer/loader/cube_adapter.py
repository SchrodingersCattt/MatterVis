"""Load cube files through the canonical structure pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..cube import CubeData, read_cube
from ..cube.bridge import cube_lattice_matrix, cube_to_cell, cube_to_raw_atoms
from .bundle_builder import build_loaded_crystal_from_atoms
from .core import LoadedCrystal


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
    """Adapt CubeData, then use the same bundle builder as every structure."""
    name = cube.path.stem if name is None else name
    title = name if title is None else title
    bundle = build_loaded_crystal_from_atoms(
        name=name,
        source_path=str(cube.path),
        raw_atoms=cube_to_raw_atoms(cube),
        cell=cube_to_cell(cube),
        M=cube_lattice_matrix(cube),
        title=title,
        preset=preset,
        source=source,
        bond_scale=bond_scale,
        bond_thresholds=bond_thresholds,
        scene_metadata_extra={"cube_data": cube},
    )
    bundle.cube_data = cube
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
    """Read a .cube file and return a canonical LoadedCrystal bundle."""
    cube = read_cube(path)
    return build_loaded_crystal_from_cube(
        cube,
        name=name,
        preset=preset,
        source=source,
        bond_scale=bond_scale,
        bond_thresholds=bond_thresholds,
    )
