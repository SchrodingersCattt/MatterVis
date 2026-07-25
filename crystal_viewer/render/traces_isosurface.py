"""Isosurface overlay traces for cube volumetric data.

Generates Mesh3d trace dicts for positive/negative orbital lobes,
coordinate-aligned to the scene's rotated frame so that Plotly's
interactive camera (rotate/pan/zoom) keeps everything synchronized.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _cube_atom_shift(scene: dict) -> np.ndarray:
    """Compute the translation from cube-native atom positions to scene draw_atoms.

    The scene's ``draw_atoms`` are formula-unit atoms that MCK may have
    shifted via PBC unwrapping. The cube's volumetric data lives in the
    original coordinate frame. This function returns the vector to translate
    the cube grid into the draw_atoms frame:

        shifted_origin = cube.origin + shift

    Note: the scene does NOT apply the R rotation to atom Cartesian coords.
    R is only used for depth sorting and camera placement.
    """
    cube = scene.get("cube_data")
    if cube is None:
        return np.zeros(3)

    # Cube atoms in their native positions
    cube_atom_carts = np.array([a.coord for a in cube.atoms], dtype=float)
    if cube_atom_carts.size == 0:
        return np.zeros(3)

    # Draw atoms in the scene (after formula-unit selection + unwrap)
    draw_atoms = scene.get("draw_atoms", [])
    if not draw_atoms:
        return np.zeros(3)
    draw_carts = np.array([a["cart"] for a in draw_atoms], dtype=float)

    return draw_carts.mean(axis=0) - cube_atom_carts.mean(axis=0)


def isosurface_mesh_extents(
    scene: dict, style: dict
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Compute the bounding box of the isosurface mesh in scene coordinates.

    Returns ``(min_xyz, max_xyz)`` or ``(None, None)`` if no cube data or
    isosurfaces are disabled.
    """
    cube = scene.get("cube_data")
    if cube is None:
        return None, None
    if not style.get("isosurface_enabled", True):
        return None, None

    shift = _cube_atom_shift(scene)
    origin = cube.origin + shift

    # The 8 corners of the cube grid volume in the shifted frame
    shape = np.asarray(cube.shape, dtype=float)
    axes = cube.axes
    corners = []
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                pt = origin + i * axes[0] * shape[0] + j * axes[1] * shape[1] + k * axes[2] * shape[2]
                corners.append(pt)
    corners_arr = np.array(corners, dtype=float)
    return corners_arr.min(axis=0), corners_arr.max(axis=0)


def isosurface_overlay_traces(scene: dict, style: dict) -> list[dict]:
    """Build isosurface Mesh3d trace dicts aligned to the scene coordinate frame.

    The scene's draw_atoms live in raw Cartesian space (no R rotation) but
    may be shifted by MCK formula-unit unwrap. The isosurface mesh must be
    shifted by the same amount so they overlap correctly. Plotly's interactive
    camera controls (rotate/pan/zoom) operate on camera.eye, not on data
    coordinates, so all traces in the same scene move together.

    Returns an empty list if the scene has no ``cube_data`` or isosurfaces
    are disabled in the style.
    """
    cube = scene.get("cube_data")
    if cube is None:
        return []
    if not style.get("isosurface_enabled", True):
        return []

    from ..cube.core import CubeData, default_isovalue

    try:
        from skimage.measure import marching_cubes
    except ImportError:
        return []

    # Read style parameters
    isovalue = style.get("isosurface_isovalue")
    percentile = float(style.get("isosurface_percentile", 98.5))
    opacity = float(style.get("isosurface_opacity", 0.55))
    positive_color = str(style.get("isosurface_positive_color", "#D55E00"))
    negative_color = str(style.get("isosurface_negative_color", "#0072B2"))
    stride = max(1, int(style.get("isosurface_stride", 2)))
    atom_mask_radius = style.get("isosurface_atom_mask_radius")
    min_volume_voxels = int(style.get("isosurface_min_volume_voxels", 0))

    # Coordinate alignment: shift cube origin by the same amount MCK
    # shifted the atoms (formula-unit unwrap). No rotation — the scene
    # does not rotate atom Cartesian coords, only the camera.
    shift = _cube_atom_shift(scene)
    aligned_origin = cube.origin + shift
    aligned_axes = cube.axes  # no rotation needed

    # Downsample
    values = cube.values[::stride, ::stride, ::stride]
    a0 = aligned_axes[0] * stride
    a1 = aligned_axes[1] * stride
    a2 = aligned_axes[2] * stride

    # Determine isovalue
    if isovalue is not None:
        iso = float(isovalue)
    else:
        iso = default_isovalue(values, percentile=percentile)

    # Optional atom masking (prevents PBC ghost lobes from tiled cubes)
    if atom_mask_radius is not None and atom_mask_radius > 0:
        from ..cube.core import CubeData as _CD, mask_to_atoms
        # Build a temporary CubeData with the strided/aligned values for masking
        # But mask_to_atoms works in the cube's native frame, so use original
        strided_cube = _CD(
            title=cube.title, comment=cube.comment, atoms=cube.atoms,
            origin=cube.origin, axes=cube.axes * stride,
            values=values, path=cube.path,
        )
        keep = mask_to_atoms(strided_cube, radius=float(atom_mask_radius))
        values = np.where(keep, values, 0.0)

    # Optional small-component filtering
    if min_volume_voxels > 0:
        try:
            from scipy.ndimage import label as ndi_label
            struct = np.ones((3, 3, 3), dtype=bool)

            def _filter(mask):
                lbl, n = ndi_label(mask, structure=struct)
                if n == 0:
                    return mask
                counts = np.bincount(lbl.ravel())
                keep = np.zeros(counts.size, dtype=bool)
                keep[1:] = counts[1:] >= min_volume_voxels
                return keep[lbl]

            pos_mask = _filter(values > iso)
            neg_mask = _filter(values < -iso)
            pos_field = np.where(pos_mask, values, 0.0)
            neg_field = np.where(neg_mask, values, 0.0)
        except ImportError:
            pos_field = values
            neg_field = values
    else:
        pos_field = values
        neg_field = values

    traces: list[dict] = []

    def _build_mesh(field: np.ndarray, level: float, color: str, name: str) -> dict | None:
        try:
            verts, faces, _, _ = marching_cubes(field, level=level)
        except (ValueError, RuntimeError):
            return None
        if verts.size == 0:
            return None
        # Transform vertices: voxel-index space → aligned Cartesian
        cart = (
            aligned_origin[None, :]
            + verts[:, 0:1] * a0[None, :]
            + verts[:, 1:2] * a1[None, :]
            + verts[:, 2:3] * a2[None, :]
        )
        n_verts = len(cart)
        return {
            "type": "mesh3d",
            "x": np.ascontiguousarray(cart[:, 0], dtype=np.float32),
            "y": np.ascontiguousarray(cart[:, 1], dtype=np.float32),
            "z": np.ascontiguousarray(cart[:, 2], dtype=np.float32),
            "i": np.ascontiguousarray(faces[:, 0], dtype=np.int16 if n_verts < 32768 else np.int32),
            "j": np.ascontiguousarray(faces[:, 1], dtype=np.int16 if n_verts < 32768 else np.int32),
            "k": np.ascontiguousarray(faces[:, 2], dtype=np.int16 if n_verts < 32768 else np.int32),
            "color": color,
            "opacity": opacity,
            "flatshading": False,
            "lighting": {"ambient": 0.85, "diffuse": 0.55, "specular": 0.2, "roughness": 0.55},
            "lightposition": {"x": 200, "y": 200, "z": 200},
            "name": name,
            "hoverinfo": "name",
            "showlegend": False,
        }

    vmax = float(np.max(pos_field))
    vmin = float(np.min(neg_field))

    if vmax >= iso:
        t = _build_mesh(pos_field, +iso, positive_color, "+ orbital")
        if t is not None:
            traces.append(t)
    if vmin <= -iso:
        t = _build_mesh(neg_field, -iso, negative_color, "- orbital")
        if t is not None:
            traces.append(t)

    return traces
