"""Isosurface overlay traces for cube volumetric data.

Generates Mesh3d trace dicts for positive/negative orbital lobes,
coordinate-aligned to the scene's rotated frame so that Plotly's
interactive camera (rotate/pan/zoom) keeps everything synchronized.
"""

from __future__ import annotations

from itertools import product

import numpy as np


def _sample_isosurface_field(
    values: np.ndarray,
    *,
    stride: int,
    periodic: bool,
) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Downsample a scalar field and optionally append wrapped endpoints.

    Cube grids store periodic samples at indices ``0 .. N-1``. Marching cubes
    needs the closing interval from the last sample back to the first one, so
    periodic mode appends an exact index ``N`` whose value is copied from index
    zero. The returned index arrays map sampled voxel coordinates back to the
    original grid and may therefore be nonuniform in their final interval.
    """
    stride = max(1, int(stride))
    indices = []
    for size in values.shape:
        axis_indices = np.arange(0, size, stride, dtype=float)
        if periodic:
            axis_indices = np.concatenate([axis_indices, np.array([float(size)])])
        indices.append(axis_indices)

    source_indices = [
        np.where(axis_indices == values.shape[axis], 0, axis_indices).astype(int)
        for axis, axis_indices in enumerate(indices)
    ]
    sampled = values[np.ix_(*source_indices)]
    return sampled, (indices[0], indices[1], indices[2])


def _interpolate_sample_indices(
    coordinates: np.ndarray, indices: np.ndarray
) -> np.ndarray:
    """Map marching-cubes coordinates onto original-grid index coordinates."""
    return np.interp(coordinates, np.arange(len(indices), dtype=float), indices)


def _periodic_component_filter(
    mask: np.ndarray, minimum: int, periodic: bool
) -> np.ndarray:
    """Filter small components, merging opposite-face labels in periodic mode."""
    from scipy.ndimage import label as ndi_label

    labels, count = ndi_label(mask, structure=np.ones((3, 3, 3), dtype=bool))
    if count == 0:
        return mask

    parent = np.arange(count + 1)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        if left == 0 or right == 0:
            return
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    if periodic:
        for axis in range(3):
            first = np.take(labels, 0, axis=axis).ravel()
            last = np.take(labels, labels.shape[axis] - 1, axis=axis).ravel()
            for left, right in zip(first, last):
                union(int(left), int(right))

    roots = np.arange(count + 1)
    for value in range(1, count + 1):
        roots[value] = find(value)
    rooted = roots[labels]
    counts = np.bincount(rooted.ravel(), minlength=count + 1)
    keep = counts >= int(minimum)
    keep[0] = False
    return keep[rooted]


def _periodic_root_labels(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Label a 3D mask with 26-neighbour connectivity across periodic faces."""
    from scipy.ndimage import label as ndi_label

    labels, count = ndi_label(mask, structure=np.ones((3, 3, 3), dtype=bool))
    parent = np.arange(count + 1)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        if left == 0 or right == 0:
            return
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    shape = np.asarray(mask.shape, dtype=int)
    for axis in range(3):
        other_axes = [index for index in range(3) if index != axis]
        for first_coord in np.argwhere(np.take(mask, 0, axis=axis)):
            first = np.zeros(3, dtype=int)
            first[axis] = 0
            first[other_axes] = first_coord
            for offsets in product((-1, 0, 1), repeat=2):
                last = first.copy()
                last[axis] = shape[axis] - 1
                last[other_axes] = (first_coord + np.asarray(offsets)) % shape[
                    other_axes
                ]
                if mask[tuple(last)]:
                    union(int(labels[tuple(first)]), int(labels[tuple(last)]))

    roots = np.arange(count + 1)
    for value in range(1, count + 1):
        roots[value] = find(value)
    return roots[labels], roots


def _unwrap_periodic_component(voxels: np.ndarray, shape: np.ndarray) -> np.ndarray:
    """Unwrap one compact voxel component from the 3-torus into index space."""
    voxel_set = {tuple(int(value) for value in voxel) for voxel in voxels}
    start = tuple(int(value) for value in voxels[0])
    unwrapped = {start: np.asarray(start, dtype=int)}
    queue = [start]
    offsets = [
        np.asarray(offset, dtype=int)
        for offset in product((-1, 0, 1), repeat=3)
        if offset != (0, 0, 0)
    ]
    while queue:
        current = queue.pop()
        current_unwrapped = unwrapped[current]
        for offset in offsets:
            neighbour = tuple((np.asarray(current) + offset) % shape)
            if neighbour not in voxel_set:
                continue
            proposal = current_unwrapped + offset
            if neighbour not in unwrapped:
                unwrapped[neighbour] = proposal
                queue.append(neighbour)
                continue
            existing = unwrapped[neighbour]
            image_delta = np.rint((proposal - existing) / shape).astype(int)
            proposal = proposal - image_delta * shape
            if np.linalg.norm(proposal - current_unwrapped) < np.linalg.norm(
                existing - current_unwrapped
            ):
                unwrapped[neighbour] = proposal
    if len(unwrapped) != len(voxel_set):
        raise RuntimeError("Periodic component unwrapping did not visit every voxel")
    result = np.asarray(
        [unwrapped[tuple(int(value) for value in voxel)] for voxel in voxels]
    )
    result -= np.floor(result.mean(axis=0) / shape).astype(int) * shape
    return result


def _sample_coordinate(
    index: np.ndarray, sampled_indices: np.ndarray, full_size: int
) -> np.ndarray:
    """Map unwrapped sampled-grid indices to original-grid index coordinates."""
    sampled_size = len(sampled_indices)
    cycles = np.floor_divide(index, sampled_size)
    local = np.mod(index, sampled_size)
    return sampled_indices[local] + cycles * full_size


def _periodic_component_meshes(
    values: np.ndarray,
    sampled_indices: tuple[np.ndarray, np.ndarray, np.ndarray],
    *,
    full_shape: tuple[int, int, int],
    level: float,
    minimum_voxels: int,
    target_indices: np.ndarray | None = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Extract one complete, canonical mesh for each compact periodic component."""
    from skimage.measure import marching_cubes

    positive = level > 0
    mask = values > level if positive else values < level
    rooted, _roots = _periodic_root_labels(mask)
    counts = np.bincount(rooted.ravel())
    background = float(values.min() if positive else values.max())
    meshes = []
    sampled_shape = np.asarray(values.shape, dtype=int)
    full_shape_array = np.asarray(full_shape, dtype=int)

    for root in range(1, len(counts)):
        if counts[root] < max(1, int(minimum_voxels)):
            continue
        voxels = np.argwhere(rooted == root)
        unwrapped = _unwrap_periodic_component(voxels, sampled_shape)
        extent = unwrapped.max(axis=0) - unwrapped.min(axis=0) + 1
        if np.any(extent >= sampled_shape):
            # A percolating component has no finite complete representative.
            # The wrapped-endpoint fallback still closes the unit-cell interval.
            return []
        lower = unwrapped.min(axis=0) - 1
        upper = unwrapped.max(axis=0) + 1
        local_axes = [np.arange(lower[axis], upper[axis] + 1) for axis in range(3)]
        modulo_axes = [
            np.mod(axis_values, sampled_shape[axis])
            for axis, axis_values in enumerate(local_axes)
        ]
        local_values = values[np.ix_(*modulo_axes)]
        local_roots = rooted[np.ix_(*modulo_axes)]
        if positive:
            local_values = np.where(
                (local_roots == root) | (local_values <= level),
                local_values,
                background,
            )
        else:
            local_values = np.where(
                (local_roots == root) | (local_values >= level),
                local_values,
                background,
            )
        try:
            vertices, faces, _normals, _levels = marching_cubes(
                local_values, level=level
            )
        except (ValueError, RuntimeError):
            continue
        sampled_coordinates = vertices + lower[None, :]
        original_coordinates = np.column_stack(
            [
                np.interp(
                    sampled_coordinates[:, axis],
                    local_axes[axis],
                    _sample_coordinate(
                        local_axes[axis],
                        sampled_indices[axis],
                        int(full_shape_array[axis]),
                    ),
                )
                for axis in range(3)
            ]
        )
        if target_indices is not None and len(target_indices):
            centroid = original_coordinates.mean(axis=0)
            candidate_shifts = np.rint(
                (target_indices - centroid[None, :]) / full_shape_array[None, :]
            ).astype(int)
            shifted_centroids = (
                centroid[None, :] + candidate_shifts * full_shape_array[None, :]
            )
            distances = np.linalg.norm(shifted_centroids - target_indices, axis=1)
            shift = candidate_shifts[int(np.argmin(distances))]
            original_coordinates = (
                original_coordinates + shift[None, :] * full_shape_array[None, :]
            )
        meshes.append((original_coordinates, faces))
    return meshes


def _cube_atom_shift(scene: dict) -> np.ndarray:
    """Translate absolute cube coordinates into the scene's cell-origin frame.

    Cube atoms and grid values share an absolute ``cube.origin``. The loader
    normalizes atom coordinates by subtracting that origin, so the scalar field
    must receive the same deterministic translation. Do not infer this shift
    from atom centroids: display modes, hidden hydrogens, disorder, and boundary
    replicas can all change the displayed atom multiplicity without changing
    the scalar coordinate frame.
    """
    cube = scene.get("cube_data")
    if cube is None:
        return np.zeros(3)
    return -np.asarray(cube.origin, dtype=float)


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
                pt = (
                    origin
                    + i * axes[0] * shape[0]
                    + j * axes[1] * shape[1]
                    + k * axes[2] * shape[2]
                )
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

    from ..cube.core import default_isovalue

    try:
        from skimage.measure import marching_cubes
    except ImportError as exc:
        from ..capabilities import resolve_requirements

        install = resolve_requirements("cube").install_command
        raise ImportError(
            "Cube isosurfaces were explicitly requested but scikit-image is "
            f"unavailable. Install with: {install}"
        ) from exc

    # Read style parameters
    isovalue = style.get("isosurface_isovalue")
    percentile = float(style.get("isosurface_percentile", 98.5))
    opacity = float(style.get("isosurface_opacity", 0.55))
    positive_color = str(style.get("isosurface_positive_color", "#D55E00"))
    negative_color = str(style.get("isosurface_negative_color", "#0072B2"))
    stride = max(1, int(style.get("isosurface_stride", 2)))
    periodic = bool(style.get("isosurface_periodic", False))
    image_policy = str(style.get("isosurface_image_policy", "cell"))
    if image_policy not in {"cell", "nearest_atom"}:
        raise ValueError(
            f"Unsupported isosurface_image_policy={image_policy!r}; expected 'cell' or 'nearest_atom'"
        )
    atom_mask_radius = style.get("isosurface_atom_mask_radius")
    min_volume_voxels = int(style.get("isosurface_min_volume_voxels", 0))

    # Coordinate alignment: shift cube origin by the same amount MCK
    # shifted the atoms (formula-unit unwrap). No rotation — the scene
    # does not rotate atom Cartesian coords, only the camera.
    shift = _cube_atom_shift(scene)
    aligned_origin = cube.origin + shift
    aligned_axes = cube.axes  # no rotation needed

    # Determine isovalue from unique source samples so wrapped endpoint planes
    # do not bias percentile-based thresholds.
    source_values = cube.values[::stride, ::stride, ::stride]

    # Determine isovalue
    if isovalue is not None:
        iso = float(isovalue)
    else:
        iso = default_isovalue(source_values, percentile=percentile)

    values, sampled_indices = _sample_isosurface_field(
        cube.values,
        stride=stride,
        periodic=periodic,
    )
    periodic_source_values, periodic_source_indices = _sample_isosurface_field(
        cube.values,
        stride=stride,
        periodic=False,
    )
    target_indices = None
    if periodic and image_policy == "nearest_atom":
        draw_atoms = scene.get("draw_atoms", [])
        if draw_atoms:
            lattice = np.asarray(cube.lattice, dtype=float)
            fractional = (
                np.asarray([atom["cart"] for atom in draw_atoms], dtype=float)
                - aligned_origin
            ) @ np.linalg.inv(lattice)
            target_indices = fractional * np.asarray(cube.shape, dtype=float)[None, :]

    # Optional atom masking (prevents PBC ghost lobes from tiled cubes)
    if atom_mask_radius is not None and atom_mask_radius > 0:
        from ..cube.core import CubeData as _CD
        from ..cube.core import mask_to_atoms

        if periodic:
            raise ValueError(
                "isosurface_atom_mask_radius is not yet supported with periodic closure; "
                "disable one of these options"
            )
        # Build a temporary CubeData with the strided/aligned values for masking
        # But mask_to_atoms works in the cube's native frame, so use original
        strided_cube = _CD(
            title=cube.title,
            comment=cube.comment,
            atoms=cube.atoms,
            origin=cube.origin,
            axes=cube.axes * stride,
            values=values,
            path=cube.path,
        )
        keep = mask_to_atoms(strided_cube, radius=float(atom_mask_radius))
        values = np.where(keep, values, 0.0)

    # Optional small-component filtering
    if min_volume_voxels > 0:
        try:

            def _filter(mask):
                return _periodic_component_filter(mask, min_volume_voxels, periodic)

            pos_mask = _filter(values > iso)
            neg_mask = _filter(values < -iso)
            pos_field = np.where(pos_mask, values, 0.0)
            neg_field = np.where(neg_mask, values, 0.0)
        except ImportError as exc:
            from ..capabilities import resolve_requirements

            install = resolve_requirements("cube").install_command
            raise ImportError(
                "Cube component filtering was explicitly requested but its "
                f"scikit-image/SciPy runtime is unavailable. Install with: {install}"
            ) from exc
    else:
        pos_field = values
        neg_field = values

    traces: list[dict] = []

    def _mesh_trace(
        original_indices: np.ndarray, faces: np.ndarray, color: str, name: str
    ) -> dict:
        # Transform vertices: original-grid index space → aligned Cartesian.
        cart = (
            aligned_origin[None, :]
            + original_indices[:, 0:1] * aligned_axes[0][None, :]
            + original_indices[:, 1:2] * aligned_axes[1][None, :]
            + original_indices[:, 2:3] * aligned_axes[2][None, :]
        )
        n_verts = len(cart)
        return {
            "type": "mesh3d",
            "x": np.ascontiguousarray(cart[:, 0], dtype=np.float32),
            "y": np.ascontiguousarray(cart[:, 1], dtype=np.float32),
            "z": np.ascontiguousarray(cart[:, 2], dtype=np.float32),
            "i": np.ascontiguousarray(
                faces[:, 0], dtype=np.int16 if n_verts < 32768 else np.int32
            ),
            "j": np.ascontiguousarray(
                faces[:, 1], dtype=np.int16 if n_verts < 32768 else np.int32
            ),
            "k": np.ascontiguousarray(
                faces[:, 2], dtype=np.int16 if n_verts < 32768 else np.int32
            ),
            "color": color,
            "opacity": opacity,
            "flatshading": False,
            "lighting": {
                "ambient": 1.0,
                "diffuse": 0.0,
                "specular": 0.0,
                "roughness": 1.0,
                "fresnel": 0.0,
            },
            "lightposition": {"x": 200, "y": 200, "z": 200},
            "name": name,
            "hoverinfo": "name",
            "showlegend": False,
        }

    def _build_meshes(
        field: np.ndarray, level: float, color: str, name: str
    ) -> list[dict]:
        if periodic:
            periodic_meshes = _periodic_component_meshes(
                periodic_source_values,
                periodic_source_indices,
                full_shape=cube.shape,
                level=level,
                minimum_voxels=min_volume_voxels,
                target_indices=target_indices,
            )
            if periodic_meshes:
                return [
                    _mesh_trace(vertices, faces, color, name)
                    for vertices, faces in periodic_meshes
                ]
        try:
            verts, faces, _, _ = marching_cubes(field, level=level)
        except (ValueError, RuntimeError):
            return []
        if verts.size == 0:
            return []
        original_indices = np.column_stack(
            [
                _interpolate_sample_indices(verts[:, axis], sampled_indices[axis])
                for axis in range(3)
            ]
        )
        return [_mesh_trace(original_indices, faces, color, name)]

    vmax = float(np.max(pos_field))
    vmin = float(np.min(neg_field))

    if vmax >= iso:
        traces.extend(_build_meshes(pos_field, +iso, positive_color, "+ orbital"))
    if vmin <= -iso:
        traces.extend(_build_meshes(neg_field, -iso, negative_color, "- orbital"))

    return traces
