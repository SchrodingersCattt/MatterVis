"""Auto-view framing for cube isosurfaces.

The fit uses displayed atom spheres plus the marching-cubes vertices that
will be drawn, in the same origin-subtracted Cartesian frame as
``draw_atoms``. Cube-grid corners, the cube box, and cell or axis lines are
not framing content.

Framing reads the vertex array stored for drawing. A density mask, a clip,
or a smoother has to replace those vertices first. This module does not
march the cube again when that list is already present, including when the
list is empty because every triangle was removed.

Plotly and the CPU rasterizer do not produce the same pixels. This module
only decides which points own the automatic camera.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

# Default ``--framing-margin``. Orthographic auto-fit already multiplied the
# projected half-extent by this value; keeping it as the flag default leaves
# structure renders unchanged when the flag is omitted.
DEFAULT_FRAMING_MARGIN = 1.12
DEFAULT_VIEW_ZOOM = 1.0

# Largest / smallest principal variance below this is treated as isotropic
# (an s-like blob). The fallback is a modest diagonal, not +z and not c*.
ISOTROPY_VARIANCE_RATIO = 1.45
# Mix a little of the mid and long axes so a lobed surface is side-on but
# not a perfectly flat silhouette. The long-axis weight stays small so the
# view cannot collapse a 2pz lobe into a disk.
OFF_AXIS_MID_WEIGHT = 0.55
OFF_AXIS_LONG_WEIGHT = 0.22
DIAGONAL_VIEW = np.array([1.0, 1.0, 0.8], dtype=float)

# Line primitives that decorate a box. They are not molecule or mesh content.
DECORATION_KINDS = frozenset(
    {"unit_cell", "cell_overlay", "axis", "lattice_axis", "compass"}
)


def cube_grid_corner_extents(
    scene: Mapping[str, Any],
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Return the eight corners of the voxel grid in the scene frame.

    This is the volumetric domain. Automatic framing must not call it.
    """
    cube = scene.get("cube_data")
    if cube is None:
        return None, None
    shift = -np.asarray(cube.origin, dtype=float)
    origin = np.asarray(cube.origin, dtype=float) + shift
    shape = np.asarray(cube.shape, dtype=float)
    axes = np.asarray(cube.axes, dtype=float)
    corners = [
        origin
        + i * axes[0] * shape[0]
        + j * axes[1] * shape[1]
        + k * axes[2] * shape[2]
        for i in (0, 1)
        for j in (0, 1)
        for k in (0, 1)
    ]
    corners_arr = np.asarray(corners, dtype=float)
    return corners_arr.min(axis=0), corners_arr.max(axis=0)


def primitive_is_frame_decoration(primitive: Any) -> bool:
    """True for cell-box and axis lines, which must not own an auto-view."""
    metadata = getattr(primitive, "metadata", None) or {}
    kind = str(metadata.get("kind", "")) if isinstance(metadata, Mapping) else ""
    if kind in DECORATION_KINDS:
        return True
    semantic_id = str(getattr(primitive, "semantic_id", ""))
    return semantic_id == "unit-cell" or semantic_id.startswith("axis:")


def viewport_has_isosurface(primitives: Any) -> bool:
    for primitive in primitives:
        metadata = getattr(primitive, "metadata", None) or {}
        kind = str(metadata.get("kind", "")) if isinstance(metadata, Mapping) else ""
        if kind == "isosurface":
            return True
        if str(getattr(primitive, "semantic_id", "")).startswith("isosurface:"):
            return True
    return False


def _finite_points(points: Any) -> np.ndarray:
    array = np.asarray(points, dtype=float)
    if array.size == 0:
        return np.empty((0, 3), dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        return np.empty((0, 3), dtype=float)
    mask = np.all(np.isfinite(array), axis=1)
    return array[mask]


def _mesh_vertex_array(entry: Any) -> np.ndarray:
    if isinstance(entry, Mapping):
        vertices = entry.get("vertices")
        if vertices is None:
            vertices = entry.get("verts")
    else:
        vertices = getattr(entry, "vertices", None)
    return _finite_points(vertices)


def _stored_mesh_vertices(scene: Mapping[str, Any], cube: Any) -> np.ndarray | None:
    entries = None
    if isinstance(scene, Mapping):
        entries = scene.get("isosurfaces")
    if entries is None and cube is not None:
        entries = getattr(cube, "surface_meshes", None)
    if not entries:
        return None
    chunks = [_mesh_vertex_array(entry) for entry in entries]
    chunks = [chunk for chunk in chunks if len(chunk)]
    if not chunks:
        return np.empty((0, 3), dtype=float)
    return np.vstack(chunks)


def frames_isosurface_content(
    scene: Mapping[str, Any] | None,
    style: Mapping[str, Any] | None = None,
) -> bool:
    """Whether automatic ranges should follow the isosurface instead of the cell."""
    if not isinstance(scene, Mapping) or scene.get("cube_data") is None:
        return False
    style = style or {}
    return bool(style.get("isosurface_enabled", True))


def isosurface_mesh_vertices(
    scene: Mapping[str, Any],
    style: Mapping[str, Any] | None = None,
) -> np.ndarray | None:
    """Return drawn isosurface vertices in the scene frame, both phases.

    ``None`` means the scene has no cube and no stored mesh. An empty array
    means nothing is drawn: either the isovalue produced no surface, or a
    later mask or clip removed every triangle. A stored ``isosurfaces`` list
    is that visible mesh and is not replaced by a fresh marching-cubes pass.
    """
    style = style or {}
    cube = scene.get("cube_data")
    if cube is not None and not style.get("isosurface_enabled", True):
        return None
    if isinstance(scene, Mapping) and "isosurfaces" in scene:
        stored = _stored_mesh_vertices(scene, None)
        return stored if stored is not None else np.empty((0, 3), dtype=float)
    if cube is None:
        return None
    if getattr(cube, "surface_meshes", None) is not None:
        stored = _stored_mesh_vertices({}, cube)
        return stored if stored is not None else np.empty((0, 3), dtype=float)
    isovalue = style.get("isosurface_isovalue", style.get("isovalue"))
    stride = int(style.get("isosurface_stride", 2) or 2)
    percentile = float(style.get("isosurface_percentile", 98.5))
    key = (
        None if isovalue is None else float(isovalue),
        stride,
        percentile,
        tuple(int(value) for value in np.asarray(cube.values).shape),
    )
    if getattr(cube, "_autoframe_key", None) == key:
        cached = getattr(cube, "_autoframe_vertices", None)
        if cached is not None:
            return np.asarray(cached, dtype=float)
    from ..cube.cpu import cube_isosurface_meshes

    try:
        meshes = cube_isosurface_meshes(
            cube,
            isovalue=None if isovalue is None else float(isovalue),
            stride=stride,
            percentile=percentile,
        )
    except ValueError:
        vertices = np.empty((0, 3), dtype=float)
    else:
        chunks = [_mesh_vertex_array(mesh) for mesh in meshes]
        chunks = [chunk for chunk in chunks if len(chunk)]
        vertices = np.vstack(chunks) if chunks else np.empty((0, 3), dtype=float)
    try:
        cube._autoframe_key = key
        cube._autoframe_vertices = vertices
    except (AttributeError, TypeError):
        pass
    return vertices


def isosurface_mesh_extents(
    scene: Mapping[str, Any],
    style: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Axis-aligned bounds of the drawn isosurface, not the voxel grid."""
    vertices = isosurface_mesh_vertices(scene, style)
    if vertices is None or len(vertices) == 0:
        return None, None
    return vertices.min(axis=0), vertices.max(axis=0)


def _positive(value: Any, default: float) -> float:
    try:
        number = float(default if value is None else value)
    except (TypeError, ValueError):
        number = float(default)
    if not np.isfinite(number) or number <= 0.0:
        raise ValueError("zoom and framing margin must be finite and greater than zero")
    return number


def zoom_and_margin(args: Any) -> tuple[float, float]:
    """Return ``(--zoom, --framing-margin)`` with the batch/TUI meaning.

    ``zoom > 1`` tightens the automatic fit. ``framing_margin`` multiplies
    the orthographic half-extent. The defaults reproduce the previous
    orthographic pad.
    """
    zoom = _positive(getattr(args, "zoom", DEFAULT_VIEW_ZOOM), DEFAULT_VIEW_ZOOM)
    margin = _positive(
        getattr(args, "framing_margin", DEFAULT_FRAMING_MARGIN),
        DEFAULT_FRAMING_MARGIN,
    )
    return zoom, margin


def scale_orthographic_half_height(
    fitted_half_height: float,
    *,
    zoom: float,
    framing_margin: float,
) -> float:
    """Apply ``half * framing_margin / zoom`` (``>1`` zooms in)."""
    return float(fitted_half_height) * float(framing_margin) / float(zoom)


def orthographic_scale_for_points(
    points: np.ndarray,
    radii: np.ndarray,
    target: np.ndarray,
    direction: np.ndarray,
    up: np.ndarray,
    *,
    aspect: float,
    zoom: float,
    framing_margin: float,
) -> float:
    """Orthographic half-height of ``points`` after zoom and framing margin.

    ``zoom > 1`` tightens the fit. ``framing_margin`` pads it. Callers that
    already have an explicit ``--ortho-scale`` do not call this.
    """
    view_to_camera = np.asarray(direction, dtype=float)
    view_to_camera = view_to_camera / max(float(np.linalg.norm(view_to_camera)), 1e-12)
    screen_up = np.asarray(up, dtype=float)
    forward = -view_to_camera
    right = np.cross(forward, screen_up)
    right_norm = float(np.linalg.norm(right))
    if right_norm < 1e-8:
        screen_up = np.asarray([0.0, 1.0, 0.0])
        right = np.cross(forward, screen_up)
        right_norm = float(np.linalg.norm(right))
    right /= right_norm
    screen_up = np.cross(right, forward)
    screen_up /= max(float(np.linalg.norm(screen_up)), 1e-12)
    half_width, half_height = projected_half_extents(
        points, radii, target, right, screen_up
    )
    return scale_orthographic_half_height(
        max(half_height, half_width / max(float(aspect), 1e-6), 0.5),
        zoom=zoom,
        framing_margin=framing_margin,
    )


def resolve_content_view(
    points: Any,
    radii: Any = None,
    *,
    width: int,
    height: int,
    zoom: float = DEFAULT_VIEW_ZOOM,
    framing_margin: float = DEFAULT_FRAMING_MARGIN,
    ortho_scale: float | None = None,
    view_direction: Any = None,
    camera_axis: str | None = None,
    camera_up: Any = None,
    target: Any = None,
) -> dict[str, Any]:
    """One automatic orthographic view for content points.

    Precedence, in order:

    - ``camera_axis`` leaves the direction to the lattice helper. This
      function returns no direction in that case.
    - ``view_direction`` replaces the content direction and does not follow
      the point cloud.
    - otherwise the content cloud chooses the direction.
    - ``ortho_scale`` replaces the fitted half-height and ignores zoom and
      framing margin.
    - otherwise the half-height is ``projected * framing_margin / zoom``.

    ``points`` must already be the visible geometry. A mask, clip, or
    smoother belongs before this call.
    """
    cloud = _finite_points(points)
    if target is None:
        centre = cloud.mean(axis=0) if len(cloud) else np.zeros(3)
    else:
        centre = np.asarray(target, dtype=float)
    pad = np.asarray([] if radii is None else radii, dtype=float)
    authority = "content"
    direction: np.ndarray | None
    up: np.ndarray | None
    if camera_axis is not None:
        authority = "camera_axis"
        direction = None
        up = None
    elif view_direction is not None:
        authority = "view_direction"
        direction = np.asarray(view_direction, dtype=float)
        norm = float(np.linalg.norm(direction))
        if norm < 1e-12:
            raise ValueError("direction must be non-zero")
        direction = direction / norm
        up = np.asarray(
            [0.0, 0.0, 1.0] if camera_up is None else camera_up, dtype=float
        )
    else:
        direction, up = view_direction_for_content(cloud)
        if camera_up is not None:
            up = np.asarray(camera_up, dtype=float)
    scale_authority = "fit"
    if ortho_scale is not None:
        scale = float(ortho_scale)
        if not np.isfinite(scale) or scale <= 0.0:
            raise ValueError("ortho_scale must be finite and positive")
        scale_authority = "ortho_scale"
    elif direction is None or up is None or len(cloud) == 0:
        scale = None
    else:
        scale = orthographic_scale_for_points(
            cloud,
            pad,
            centre,
            direction,
            up,
            aspect=float(width) / float(height),
            zoom=zoom,
            framing_margin=framing_margin,
        )
    return {
        "authority": authority,
        "scale_authority": scale_authority,
        "direction": direction,
        "up": up,
        "ortho_scale": scale,
        "target": centre,
    }


def scale_perspective_distance(
    distance: float,
    *,
    zoom: float,
    framing_margin: float,
) -> float:
    """Scale an existing perspective fit distance.

    Dividing by the default margin keeps ``--zoom 1 --framing-margin 1.12``
    equal to the historical ``--camera-distance`` fit. Larger zoom moves the
    eye closer. An explicit camera position is not passed through here.
    """
    return (
        float(distance)
        * (float(framing_margin) / DEFAULT_FRAMING_MARGIN)
        / float(zoom)
    )


def _oriented(vector: np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=float)
    index = int(np.argmax(np.abs(array)))
    if array[index] < 0.0:
        array = -array
    return array


def _orthogonal_up(direction: np.ndarray, up_hint: np.ndarray) -> np.ndarray:
    forward = np.asarray(direction, dtype=float)
    forward = forward / max(float(np.linalg.norm(forward)), 1e-12)
    hint = np.asarray(up_hint, dtype=float)
    projected = hint - float(np.dot(hint, forward)) * forward
    if float(np.linalg.norm(projected)) < 1e-8:
        for fallback in (
            np.array([0.0, 0.0, 1.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
        ):
            projected = fallback - float(np.dot(fallback, forward)) * forward
            if float(np.linalg.norm(projected)) >= 1e-8:
                break
    return projected / max(float(np.linalg.norm(projected)), 1e-12)


def view_direction_for_content(
    points: Any,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a view direction and up vector for molecule-plus-mesh points.

    The direction is from the scene toward the camera. A lobed or elongated
    cloud is viewed mostly from the thin side, with a small tilt, so the long
    axis lies across the frame instead of pointing at the camera. A nearly
    round cloud uses a fixed diagonal.
    """
    cloud = _finite_points(points)
    diagonal = DIAGONAL_VIEW / float(np.linalg.norm(DIAGONAL_VIEW))
    if len(cloud) < 3:
        return diagonal, _orthogonal_up(diagonal, np.array([0.0, 0.0, 1.0]))
    centered = cloud - cloud.mean(axis=0)
    covariance = centered.T @ centered
    variances, axes = np.linalg.eigh(covariance)
    order = np.argsort(variances)
    variances = np.asarray(variances, dtype=float)[order]
    axes = np.asarray(axes, dtype=float)[:, order]
    thin = _oriented(axes[:, 0])
    mid = _oriented(axes[:, 1])
    long_axis = _oriented(axes[:, 2])
    small = max(float(variances[0]), 0.0)
    large = max(float(variances[2]), 0.0)
    if large <= 1e-12 or large < small * ISOTROPY_VARIANCE_RATIO:
        return diagonal, _orthogonal_up(diagonal, np.array([0.0, 0.0, 1.0]))
    direction = thin + OFF_AXIS_MID_WEIGHT * mid + OFF_AXIS_LONG_WEIGHT * long_axis
    norm = float(np.linalg.norm(direction))
    if norm < 1e-12:
        return diagonal, _orthogonal_up(diagonal, np.array([0.0, 0.0, 1.0]))
    direction = direction / norm
    if abs(float(np.dot(direction, long_axis))) > 0.55:
        direction = thin + OFF_AXIS_MID_WEIGHT * mid
        direction = direction / max(float(np.linalg.norm(direction)), 1e-12)
    return direction, _orthogonal_up(direction, long_axis)


def atom_sphere_samples(
    atoms: Any,
    *,
    show_hydrogen: bool = True,
    representation: str = "ball_stick",
    atom_scale: float = 1.0,
    bond_radius: float | None = None,
    min_atom_radius: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return atom centres and rendered radii for the automatic fit."""
    from .radius import atom_visual_radius

    centers: list[np.ndarray] = []
    radii: list[float] = []
    for atom in atoms or []:
        if not isinstance(atom, Mapping):
            continue
        element = str(atom.get("elem") or atom.get("symbol") or atom.get("element") or "C")
        if not show_hydrogen and element.strip().capitalize() in {"H", "D"}:
            continue
        cart = np.asarray(atom.get("cart", atom.get("position")), dtype=float)
        if cart.shape != (3,) or not np.all(np.isfinite(cart)):
            continue
        raw = atom.get("atom_radius", atom.get("radius", 0.5))
        try:
            raw_radius = float(raw)
        except (TypeError, ValueError):
            raw_radius = 0.5
        rendered = atom_visual_radius(
            raw_radius,
            element=element,
            representation=representation,
            atom_scale=atom_scale,
            bond_radius=bond_radius,
            min_atom_radius=min_atom_radius,
            coordination=(
                str(representation).lower().replace("-", "_") == "ball_stick"
                and bool(atom.get("_coordination_site", False))
            ),
        )
        centers.append(cart)
        radii.append(float(rendered))
    if not centers:
        return np.empty((0, 3), dtype=float), np.empty((0,), dtype=float)
    return np.asarray(centers, dtype=float), np.asarray(radii, dtype=float)


def content_aabb(
    points: np.ndarray,
    radii: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Bounds of points expanded by per-point radii."""
    cloud = _finite_points(points)
    if len(cloud) == 0:
        raise ValueError("isosurface auto-view has no finite content")
    if radii is None or len(radii) != len(cloud):
        pad = np.zeros((len(cloud), 1), dtype=float)
    else:
        pad = np.asarray(radii, dtype=float).reshape(-1, 1)
    return (cloud - pad).min(axis=0), (cloud + pad).max(axis=0)


def bundle_cube(bundle: Any, scene: Mapping[str, Any] | None = None) -> Any:
    cube = getattr(bundle, "cube_data", None)
    if cube is None and isinstance(scene, Mapping):
        cube = scene.get("cube_data")
    return cube


def _style_from_args(args: Any) -> dict[str, Any]:
    opacity = getattr(args, "isosurface_opacity", None)
    color_range = getattr(args, "isosurface_color_range", None)
    if color_range is not None:
        color_range = (float(color_range[0]), float(color_range[1]))
    return {
        "isosurface_enabled": True,
        "isosurface_isovalue": getattr(args, "isovalue", None),
        "isosurface_stride": int(getattr(args, "isosurface_stride", 2) or 2),
        "isosurface_percentile": float(getattr(args, "isosurface_percentile", 98.5)),
        "isosurface_opacity": 0.55 if opacity is None else float(opacity),
        "isosurface_positive_color": getattr(
            args, "isosurface_positive_color", "#D55E00"
        ),
        "isosurface_negative_color": getattr(
            args, "isosurface_negative_color", "#0072B2"
        ),
        "isosurface_ambient": float(getattr(args, "isosurface_ambient", 0.68)),
        "isosurface_diffuse": float(getattr(args, "isosurface_diffuse", 0.32)),
        "isosurface_colormap": getattr(args, "isosurface_colormap", "bgr"),
        "isosurface_color_range": color_range,
        "isosurface_color_center": getattr(args, "isosurface_color_center", None),
    }


def _density_mask_arguments(args: Any) -> tuple[bool, Any, float | None]:
    """Density cube and cutoff for the same mask the draw path applies.

    The mask stays off unless the caller sets it. A stored path and cutoff
    are not read while it is off, so they cannot clip an isosurface by
    themselves.
    """

    enabled = bool(getattr(args, "isosurface_density_mask", False))
    if not enabled:
        return False, None, None
    density_path = getattr(args, "isosurface_density_cube", None)
    cutoff = getattr(args, "isosurface_density_cutoff", None)
    if not density_path or cutoff is None:
        raise ValueError(
            "density mask requires a density cube and ρ_cut > 0 in "
            "e/Bohr^3; the isosurface was not masked and no cutoff "
            "was chosen automatically"
        )
    from ..cube.io import read_cube

    return True, read_cube(density_path), float(cutoff)


def ensure_structure_isosurfaces(structure: Any, args: Any) -> None:
    """Extract meshes with the same settings the general renderer will draw.

    A later ``ensure_cube_isosurfaces`` call with those settings reuses the
    cache. When the density mask is on, this extraction uses that cube and
    cutoff, so the vertices stored for the camera are the masked mesh.
    Those vertices are the unsmoothed marching-cubes result unless a caller
    already replaced them with a smoothed or clipped mesh.
    """
    from ..cube.cpu import ensure_cube_isosurfaces

    style = _style_from_args(args)
    color_cube = None
    color_path = getattr(args, "isosurface_color_cube", None)
    if color_path:
        from ..cube.io import read_cube

        color_cube = read_cube(color_path)
    density_mask, density_cube, density_cutoff = _density_mask_arguments(args)
    ensure_cube_isosurfaces(
        structure,
        isovalue=style["isosurface_isovalue"],
        opacity=style["isosurface_opacity"],
        positive_color=style["isosurface_positive_color"],
        negative_color=style["isosurface_negative_color"],
        stride=style["isosurface_stride"],
        ambient=style["isosurface_ambient"],
        diffuse=style["isosurface_diffuse"],
        color_cube=color_cube,
        color_range=style["isosurface_color_range"],
        color_center=style["isosurface_color_center"],
        colormap=style["isosurface_colormap"],
        density_mask=density_mask,
        density_cube=density_cube,
        density_cutoff=density_cutoff,
    )


def isosurface_content_fit(structure: Any, bundle: Any, scene: Any, args: Any, prepare: bool = True):
    """Atom spheres plus drawn isosurface vertices, or ``None`` for non-cubes.

    Grid corners and cell edges are not included. ``prepare`` extracts meshes
    once; later animation frames reuse that cache.
    """
    cube = bundle_cube(bundle, scene if isinstance(scene, Mapping) else None)
    stored = isinstance(scene, Mapping) and "isosurfaces" in scene
    if cube is None and not stored:
        return None
    # A list already on the scene is the visible mesh, including an empty
    # list left by a mask or clip. Re-extracting here would ignore that.
    if prepare and cube is not None and not stored:
        ensure_structure_isosurfaces(structure, args)
    return content_samples_for_scene(
        bundle, scene if isinstance(scene, Mapping) else {}, args
    )


def absorb_content_fit(
    structure: Any,
    bundle: Any,
    scene: Any,
    args: Any,
    target: np.ndarray,
    radius: float,
    fit_points: np.ndarray,
):
    """Replace a structure fit with isosurface content when a cube is present."""
    fit_radii = np.zeros(len(fit_points), dtype=float)
    mode = "structure"
    orientation = None
    content = isosurface_content_fit(structure, bundle, scene, args)
    if content is None:
        return fit_points, fit_radii, target, radius, mode, orientation
    fit_points, fit_radii, orientation = content
    if len(fit_points):
        mode = "isosurface_content"
        minimum, maximum = content_aabb(fit_points, fit_radii)
        target = 0.5 * (minimum + maximum)
        radius = max(float(np.linalg.norm(0.5 * (maximum - minimum))), 0.5)
    return fit_points, fit_radii, target, radius, mode, orientation


def append_fit_points(
    fit_points: np.ndarray,
    fit_radii: np.ndarray,
    extra: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    if not len(extra):
        return fit_points, fit_radii
    return (
        np.vstack((fit_points, extra)),
        np.concatenate((fit_radii, np.zeros(len(extra), dtype=float))),
    )


def bounds_corners(points: np.ndarray, radii: np.ndarray) -> np.ndarray:
    """Expand centres by radii so a shared bounds accumulator includes spheres."""
    if len(radii) == len(points) and np.any(radii):
        return np.vstack((points - radii[:, None], points + radii[:, None]))
    return points


def content_direction_or_none(mode: str, args: Any, orientation: np.ndarray | None):
    """Isosurface auto-view direction, or ``None`` when a lattice face is used."""
    if mode != "isosurface_content" or getattr(args, "camera_axis", None) is not None:
        return None
    if orientation is None or len(orientation) == 0:
        return None
    resolved = resolve_content_view(orientation, width=1, height=1)
    return resolved["direction"], resolved["up"]


def projected_half_extents(
    points: np.ndarray,
    radii: np.ndarray,
    target: np.ndarray,
    right: np.ndarray,
    screen_up: np.ndarray,
) -> tuple[float, float]:
    relative = np.asarray(points, dtype=float) - np.asarray(target, dtype=float)
    pad = np.asarray(radii, dtype=float)
    if pad.shape != (len(relative),):
        pad = np.zeros(len(relative), dtype=float)
    half_width = float(np.max(np.abs(relative @ right) + pad))
    half_height = float(np.max(np.abs(relative @ screen_up) + pad))
    return half_width, half_height


def remember_camera_fit(
    args: Any,
    *,
    mode: str,
    target: Any,
    points: Any,
    radii: Any,
    includes_cell: bool,
) -> None:
    """Attach the fit AABB used by ``mat-vis render --json``."""
    cloud = np.asarray(points, dtype=float)
    pad = np.asarray(radii, dtype=float)
    if cloud.ndim == 2 and cloud.shape[1:] == (3,) and len(cloud):
        if pad.shape == (len(cloud),):
            minimum = np.min(cloud - pad[:, None], axis=0)
            maximum = np.max(cloud + pad[:, None], axis=0)
        else:
            minimum = cloud.min(axis=0)
            maximum = cloud.max(axis=0)
    else:
        centre = np.asarray(target, dtype=float)
        minimum = maximum = centre
    try:
        args.camera_fit_record = {
            "mode": mode,
            "target": [float(value) for value in np.asarray(target, dtype=float)],
            "aabb_min": [float(value) for value in minimum],
            "aabb_max": [float(value) for value in maximum],
            "includes_cell": bool(includes_cell),
        }
    except (AttributeError, TypeError):
        return


def fit_primitives(primitives: Any, *, width: int, height: int):
    """Fit a camera to render primitives.

    Structure-only plans keep the historical bounding-sphere fit, including
    cell edges. An isosurface plan uses atom, bond, and mesh vertices and
    leaves cell-box and axis lines out of the content cloud.
    """
    from .contracts import (
        CameraSpec,
        LinePrimitive,
        TextPrimitive,
        TriangleMeshPrimitive,
    )

    isosurface_fit = viewport_has_isosurface(primitives)
    points: list[np.ndarray] = []
    for primitive in primitives:
        if isosurface_fit and primitive_is_frame_decoration(primitive):
            continue
        if isinstance(primitive, TriangleMeshPrimitive):
            points.append(primitive.vertices)
        elif isinstance(primitive, LinePrimitive):
            points.append(primitive.segments.reshape(-1, 3))
        elif isinstance(primitive, TextPrimitive):
            points.append(np.asarray([primitive.position]))
    if not points:
        return CameraSpec()
    combined = np.vstack(points)
    combined = combined[np.all(np.isfinite(combined), axis=1)]
    if len(combined) == 0:
        return CameraSpec()
    minimum, maximum = combined.min(axis=0), combined.max(axis=0)
    target = 0.5 * (minimum + maximum)
    radius = max(float(np.linalg.norm(combined - target, axis=1).max()), 0.5)
    if isosurface_fit:
        resolved = resolve_content_view(
            combined,
            width=width,
            height=height,
            target=target,
        )
        direction = resolved["direction"]
        up = resolved["up"]
        ortho_scale = resolved["ortho_scale"]
    else:
        direction = np.asarray([1.0, 1.0, 0.8], dtype=float)
        direction = direction / np.linalg.norm(direction)
        up = np.asarray([0.0, 0.0, 1.0])
        aspect = float(width) / float(height)
        ortho_scale = radius * 1.18 / min(1.0, aspect)
    distance = max(radius * 3.2, 2.0)
    return CameraSpec.looking_along(
        direction,
        target=target,
        up=up,
        distance=distance,
        projection="orthographic",
        ortho_scale=ortho_scale,
        near=max(1e-3, distance - radius * 2.2),
        far=distance + radius * 2.2,
    )


def content_samples_for_scene(
    bundle: Any,
    scene: Mapping[str, Any],
    args: Any,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Return ``(fit points, radii, orientation cloud)`` for a cube scene.

    ``None`` when the bundle is not a cube. Fit points are atom centres and
    mesh vertices. Radii are the rendered atom radii (zero on mesh vertices).
    The orientation cloud is the same positions without duplicating sphere
    corners, so principal axes follow the molecule and the surface.
    """
    cube = bundle_cube(bundle, scene)
    if cube is None and not (
        isinstance(scene, Mapping) and "isosurfaces" in scene
    ):
        return None
    if isinstance(scene, Mapping) and scene.get("cube_data") is None and cube is not None:
        scene = {**scene, "cube_data": cube}
    vertices = isosurface_mesh_vertices(scene, _style_from_args(args))
    if vertices is None:
        stored = _stored_mesh_vertices(scene, cube)
        vertices = stored if stored is not None else np.empty((0, 3), dtype=float)
    centers, radii = atom_sphere_samples(
        scene.get("draw_atoms") if isinstance(scene, Mapping) else (),
        show_hydrogen=bool(getattr(args, "show_hydrogen", True)),
        representation=str(getattr(args, "style", "ball_stick") or "ball_stick"),
        atom_scale=float(getattr(args, "atom_scale", 1.0) or 1.0),
        bond_radius=getattr(args, "bond_radius", None),
        min_atom_radius=getattr(args, "min_atom_radius", None),
    )
    chunks = []
    radius_chunks = []
    if len(centers):
        chunks.append(centers)
        radius_chunks.append(radii)
    if vertices is not None and len(vertices):
        chunks.append(vertices)
        radius_chunks.append(np.zeros(len(vertices), dtype=float))
    if not chunks:
        return None
    points = np.vstack(chunks)
    point_radii = np.concatenate(radius_chunks)
    return points, point_radii, points


__all__ = [
    "DEFAULT_FRAMING_MARGIN",
    "DEFAULT_VIEW_ZOOM",
    "DIAGONAL_VIEW",
    "atom_sphere_samples",
    "bundle_cube",
    "content_aabb",
    "content_samples_for_scene",
    "cube_grid_corner_extents",
    "ensure_structure_isosurfaces",
    "frames_isosurface_content",
    "isosurface_mesh_extents",
    "isosurface_mesh_vertices",
    "orthographic_scale_for_points",
    "primitive_is_frame_decoration",
    "resolve_content_view",
    "scale_orthographic_half_height",
    "scale_perspective_distance",
    "view_direction_for_content",
    "viewport_has_isosurface",
    "zoom_and_margin",
]
