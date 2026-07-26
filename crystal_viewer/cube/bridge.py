"""Bridge between CubeData and the standard crystal structure pipeline.

Converts cube-file atoms and lattice into the ``raw_atoms`` list-of-dicts
format expected by :func:`~crystal_viewer.scene.core.build_scene_from_atoms`
and the ``(cell, M)`` pair used throughout the loader/scene pipeline.

Also provides :func:`build_cube_figure` — the recommended entry point for
rendering a cube file with the standard crystal pipeline + isosurface overlay.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .core import CubeData


def cube_lattice_matrix(cube: CubeData) -> np.ndarray:
    """Return the 3×3 lattice matrix M (rows = cell vectors in Å)."""
    return np.asarray(cube.lattice, dtype=float)


def cube_to_cell(cube: CubeData):
    """Derive a ``gemmi.UnitCell`` from the cube lattice vectors.

    Computes cell lengths and angles from the 3×3 Cartesian matrix.
    """
    import gemmi

    M = cube_lattice_matrix(cube)
    a_vec, b_vec, c_vec = M[0], M[1], M[2]
    a = float(np.linalg.norm(a_vec))
    b = float(np.linalg.norm(b_vec))
    c = float(np.linalg.norm(c_vec))

    alpha = float(np.degrees(np.arccos(np.clip(np.dot(b_vec, c_vec) / (b * c), -1, 1))))
    beta = float(np.degrees(np.arccos(np.clip(np.dot(a_vec, c_vec) / (a * c), -1, 1))))
    gamma = float(np.degrees(np.arccos(np.clip(np.dot(a_vec, b_vec) / (a * b), -1, 1))))

    return gemmi.UnitCell(a, b, c, alpha, beta, gamma)


def cube_to_raw_atoms(cube: CubeData) -> list[dict[str, Any]]:
    """Convert ``CubeData.atoms`` to the standard ``raw_atoms`` format.

    Each dict contains the keys consumed by ``build_scene_from_atoms``:
    ``elem``, ``cart``, ``frac``, ``label``, ``occ``, ``dg``, ``da``.
    """
    M = cube_lattice_matrix(cube)
    M_inv = np.linalg.inv(M)

    atoms: list[dict[str, Any]] = []
    # Counter per element for labeling (C1, C2, N1, ...)
    elem_count: dict[str, int] = {}
    for atom in cube.atoms:
        elem = atom.element
        elem_count[elem] = elem_count.get(elem, 0) + 1
        label = f"{elem}{elem_count[elem]}"

        cart = np.asarray(atom.coord, dtype=float)
        frac = M_inv @ cart

        atoms.append({
            "elem": elem,
            "cart": cart,
            "frac": frac,
            "label": label,
            "_asym_label": label,
            "occ": 1.0,
            "dg": ".",
            "da": ".",
            "_symop_index": 0,
            "_bond_partners": (),
            "_bond_lengths": {},
            "_has_bond_table": False,
        })
    return atoms


def build_cube_figure(
    path,
    *,
    isovalue: float | None = None,
    opacity: float | None = None,
    camera: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    display_mode: str = "formula_unit",
    show_hydrogen: bool = False,
):
    """Render a cube file through the unified crystal pipeline with isosurface overlay.

    This is the recommended replacement for :func:`build_orbital_panel_figure`.
    The structure (atoms, bonds) goes through the full rendering pipeline
    (MCK bonds, correct colors/materials, display modes) and the volumetric data
    is rendered as an isosurface overlay on top.

    Parameters
    ----------
    path : str or Path
        Path to the .cube file.
    isovalue : float, optional
        Isosurface threshold. If None, auto-detected from the 98.5th percentile
        of absolute non-zero values.
    opacity : float, optional
        Isosurface opacity (0–1). Default 0.55.
    camera : dict, optional
        Plotly camera dict used for both the 3D scene and the projected
        lattice compass. Pass the final camera here rather than mutating
        ``fig.layout.scene.camera`` afterwards; the compass is baked into
        static figures when the figure is built.
    style : dict, optional
        Style overrides (isosurface_*, atom_scale, bond_radius, etc.).
    display_mode : str
        Display mode for the structure ("formula_unit", "unit_cell", etc.).
    show_hydrogen : bool
        Whether to show hydrogen atoms.

    Returns
    -------
    plotly.graph_objects.Figure
    """
    from pathlib import Path as _Path

    from ..config.schema import BUILTIN_STYLE
    from ..loader.core import build_bundle_scene
    from ..loader.cube_adapter import load_cube_file
    from ..render.figures import build_figure

    cube_path = _Path(path)
    bundle = load_cube_file(cube_path)

    scene = build_bundle_scene(
        bundle, display_mode=display_mode, show_hydrogen=show_hydrogen,
    )
    # Ensure cube_data is on the scene for the isosurface renderer
    if scene.get("cube_data") is None:
        scene["cube_data"] = bundle.cube_data

    merged_style = dict(BUILTIN_STYLE)
    # Apply convenience kwargs before user style dict (user dict wins)
    if isovalue is not None:
        merged_style["isosurface_isovalue"] = isovalue
    if opacity is not None:
        merged_style["isosurface_opacity"] = opacity
    if camera is not None:
        merged_style["camera"] = camera
    if style:
        merged_style.update(style)

    return build_figure(scene, merged_style)
