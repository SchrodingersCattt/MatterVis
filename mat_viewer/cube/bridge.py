"""Bridge between CubeData and the standard crystal structure pipeline.

Converts cube-file atoms and lattice into the ``raw_atoms`` list-of-dicts
format expected by :func:`~mat_viewer.scene.core.build_scene_from_atoms`
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

        cart = np.asarray(atom.coord, dtype=float) - cube.origin
        # Cell vectors are rows, and cube atom positions are absolute
        # Cartesian coordinates relative to ``cube.origin``.
        frac = cart @ M_inv

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
    periodic: bool | None = None,
    periodic_image_policy: str | None = None,
    bond_scale: float | None = None,
    bond_thresholds: dict[tuple[str, str], float] | None = None,
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
    periodic : bool, optional
        Close the scalar grid across opposite unit-cell faces before mesh
        extraction. Use ``True`` for periodic densities and ``False`` for
        isolated molecular or vacuum cubes. If omitted, the style default is
        used. A value in ``style["isosurface_periodic"]`` takes precedence.
    periodic_image_policy : {"cell", "nearest_atom"}, optional
        Select the lattice image of each compact periodic component. ``cell``
        keeps a canonical representative near the base cell;
        ``nearest_atom`` places it nearest a displayed atom image, which is
        useful when unit-cell mode materializes complete boundary fragments.
    bond_scale : float, optional
        Global coefficient applied to MolCrysKit bonding thresholds for both
        molecule grouping and visible bonds. ``style["mck_bond_scale"]`` takes
        precedence; otherwise config ``mck_overrides.bond_scale`` is used, with
        MolCrysKit's default 1.0 as the final fallback.
    bond_thresholds : dict, optional
        Explicit element-pair thresholds in Å. The global ``bond_scale`` also
        multiplies these overrides, matching MolCrysKit semantics.

    Returns
    -------
    plotly.graph_objects.Figure
    """
    from pathlib import Path as _Path

    from ..config import current_config
    from ..config.schema import BUILTIN_STYLE
    from ..loader.core import build_bundle_scene
    from ..loader.cube_adapter import load_cube_file
    from ..render.figures import build_figure

    if bond_thresholds is not None:
        for pair, value in bond_thresholds.items():
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("bond_thresholds keys must be 2-tuples of element symbols")
            numeric = float(value)
            if not np.isfinite(numeric) or numeric <= 0:
                raise ValueError("bond_thresholds values must be finite and positive")
            if numeric * float(bond_scale if bond_scale is not None else 1.0) > 12.0:
                raise ValueError("effective bond cutoff exceeds the 12.0 Å candidate-search guard")

    merged_style = dict(BUILTIN_STYLE)
    # Apply convenience kwargs before user style dict (user dict wins)
    if isovalue is not None:
        merged_style["isosurface_isovalue"] = isovalue
    if opacity is not None:
        merged_style["isosurface_opacity"] = opacity
    if camera is not None:
        merged_style["camera"] = camera
    if periodic is not None:
        merged_style["isosurface_periodic"] = bool(periodic)
    if periodic_image_policy is not None:
        merged_style["isosurface_image_policy"] = str(periodic_image_policy)
    if style:
        merged_style.update(style)

    configured_scale = current_config().mck_overrides.get("bond_scale", None)
    effective_bond_scale = merged_style.get("mck_bond_scale", bond_scale)
    if effective_bond_scale is None:
        effective_bond_scale = configured_scale
    if effective_bond_scale is None:
        effective_bond_scale = 1.0
    effective_bond_scale = float(effective_bond_scale)
    if not np.isfinite(effective_bond_scale) or effective_bond_scale <= 0:
        raise ValueError("bond_scale must be finite and positive")
    merged_style["mck_bond_scale"] = effective_bond_scale

    cube_path = _Path(path)
    bundle = load_cube_file(
        cube_path,
        bond_scale=effective_bond_scale,
        bond_thresholds=bond_thresholds,
    )

    scene = build_bundle_scene(
        bundle, display_mode=display_mode, show_hydrogen=show_hydrogen,
    )
    # Ensure cube_data is on the scene for the isosurface renderer
    if scene.get("cube_data") is None:
        scene["cube_data"] = bundle.cube_data

    return build_figure(scene, merged_style)
