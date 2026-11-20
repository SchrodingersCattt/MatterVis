"""General-renderer camera fit for ``mat-vis render``.

Kept out of ``cli.py`` so the command router stays inside its line budget.
Structure-only fits are unchanged. Cube isosurfaces use atom spheres and
mesh vertices, and ``--zoom`` / ``--framing-margin`` scale that fit.
"""

from __future__ import annotations

import argparse


def _scene_fit(
    bundle,
    *,
    display: str,
    show_hydrogen: bool,
    show_cell: bool,
    include_boundary_replicas: bool = True,
    include_cross_boundary_bond_endpoints: bool = True,
):
    import numpy as np

    scene = getattr(bundle, "scene", {}) or {}
    if all(
        hasattr(bundle, name)
        for name in ("raw_atoms", "scene_cache", "M", "cell", "formula_unit_atoms")
    ):
        from ..loader.core import build_bundle_scene

        scene = build_bundle_scene(
            bundle,
            display_mode=display,
            show_hydrogen=show_hydrogen,
            include_boundary_replicas=include_boundary_replicas,
            include_cross_boundary_bond_endpoints=include_cross_boundary_bond_endpoints,
        )
    bounds = scene.get("bounds") or {}
    center = np.asarray(bounds.get("center", (0.0, 0.0, 0.0)), dtype=float)
    radius = 0.0
    mins = np.asarray(bounds.get("mins", ()), dtype=float)
    maxs = np.asarray(bounds.get("maxs", ()), dtype=float)
    fit_points = np.empty((0, 3), dtype=float)
    cell_vertices = None
    if mins.shape == (3,) and maxs.shape == (3,) and np.all(np.isfinite((mins, maxs))):
        if display == "unit_cell" or show_cell:
            matrix = np.asarray(getattr(bundle, "M", scene.get("M")), dtype=float)
            if matrix.shape == (3, 3) and np.all(np.isfinite(matrix)):
                fractions = np.asarray(
                    [
                        [0, 0, 0],
                        [1, 0, 0],
                        [0, 1, 0],
                        [0, 0, 1],
                        [1, 1, 0],
                        [1, 0, 1],
                        [0, 1, 1],
                        [1, 1, 1],
                    ],
                    dtype=float,
                )
                cell_vertices = fractions @ matrix
                mins = np.minimum(mins, cell_vertices.min(axis=0))
                maxs = np.maximum(maxs, cell_vertices.max(axis=0))
        fit_points = np.asarray(
            [
                [x, y, z]
                for x in (mins[0], maxs[0])
                for y in (mins[1], maxs[1])
                for z in (mins[2], maxs[2])
            ],
            dtype=float,
        )
        if display == "unit_cell" and cell_vertices is not None:
            center = np.asarray(cell_vertices, dtype=float).mean(axis=0)
            radius = float(np.linalg.norm(fit_points - center, axis=1).max())
        else:
            center = (mins + maxs) * 0.5
            radius = float(np.linalg.norm((maxs - mins) * 0.5))
    if radius <= 1.0e-9:
        ranges = np.asarray(bounds.get("ranges", ()), dtype=float)
        if ranges.shape == (3,) and np.all(np.isfinite(ranges)):
            radius = float(np.linalg.norm(ranges * 0.5))
    if center.shape != (3,) or not np.all(np.isfinite(center)):
        center = np.zeros(3, dtype=float)
    if not len(fit_points):
        fit_points = np.asarray([center - radius, center + radius], dtype=float)
    return scene, center, max(radius, 1.0), fit_points


def _camera_spec(
    structure,
    args: argparse.Namespace,
    *,
    display: str,
    topology_data=None,
):
    import numpy as np

    from ..agent_topology import topology_fit_points
    from ..cli import _effective_show_cell
    from .cli_controls import _is_animation_output
    from .contracts import CameraSpec
    from .isosurface_framing import (
        absorb_content_fit,
        append_fit_points,
        bounds_corners,
        content_direction_or_none,
        isosurface_content_fit,
        orthographic_scale_for_points,
        remember_camera_fit,
        scale_perspective_distance,
        zoom_and_margin,
    )

    selected = structure.frames[0]
    matrix = np.asarray(getattr(selected.bundle, "M", np.eye(3)), dtype=float)
    camera_target = getattr(args, "camera_target", None)
    camera_clip = getattr(args, "camera_clip", None)
    requested_ortho_scale = getattr(args, "ortho_scale", None)
    requested_field_of_view = getattr(args, "field_of_view", None)
    include_boundary_replicas = getattr(args, "include_boundary_replicas", None)
    if include_boundary_replicas is None:
        include_boundary_replicas = True
    include_cross_boundary_bond_endpoints = getattr(
        args, "style", "ball_stick"
    ) not in {"ball", "space_filling"}
    scene, target, radius, fit_points = _scene_fit(
        selected.bundle,
        display=display,
        show_hydrogen=args.show_hydrogen,
        show_cell=_effective_show_cell(structure, args),
        include_boundary_replicas=include_boundary_replicas,
        include_cross_boundary_bond_endpoints=include_cross_boundary_bond_endpoints,
    )
    fit_points, fit_radii, target, radius, fit_mode, orientation_cloud = (
        absorb_content_fit(
            structure, selected.bundle, scene, args, target, radius, fit_points
        )
    )
    topology_points = topology_fit_points(topology_data)
    if len(topology_points):
        fit_points, fit_radii = append_fit_points(fit_points, fit_radii, topology_points)
        radius = max(radius, float(np.linalg.norm(fit_points - target, axis=1).max()))
    if _is_animation_output(args) and len(structure.frames) > 1:
        from .viewport import ViewportAccumulator

        accumulator = ViewportAccumulator()
        accumulator.update_points(bounds_corners(fit_points, fit_radii))
        for frame in structure.frames[1:]:
            frame_scene, _, _, frame_fit_points = _scene_fit(
                frame.bundle,
                display=display,
                show_hydrogen=args.show_hydrogen,
                show_cell=_effective_show_cell(structure, args),
                include_boundary_replicas=include_boundary_replicas,
                include_cross_boundary_bond_endpoints=(
                    include_cross_boundary_bond_endpoints
                ),
            )
            frame_content = isosurface_content_fit(
                structure, frame.bundle, frame_scene, args, prepare=False
            )
            if frame_content is not None and len(frame_content[0]):
                frame_fit_points = bounds_corners(frame_content[0], frame_content[1])
            accumulator.update_points(frame_fit_points)
        fit_points = accumulator.fit_points()
        fit_radii = np.zeros(len(fit_points), dtype=float)
        minimum = fit_points.min(axis=0)
        maximum = fit_points.max(axis=0)
        target = 0.5 * (minimum + maximum)
        radius = float(np.linalg.norm(0.5 * (maximum - minimum)))
    if camera_target is not None:
        target = np.asarray(camera_target, dtype=float)
    zoom, framing_margin = zoom_and_margin(args)
    aspect = max(float(args.width) / float(args.height), 1.0e-6)
    ortho_scale = (
        float(requested_ortho_scale)
        if requested_ortho_scale is not None
        else radius * 1.15 / min(aspect, 1.0)
    )
    field_of_view = (
        float(requested_field_of_view) if requested_field_of_view is not None else 45.0
    )

    if args.camera_position is not None:
        position = np.asarray(args.camera_position, dtype=float)
        up = np.asarray(args.camera_up or matrix[1], dtype=float)
        absolute_distance = float(np.linalg.norm(position - target))
        near = max(radius * 1.0e-4, absolute_distance - 1.5 * radius)
        far = max(near * 2.0, absolute_distance + 1.5 * radius)
        if camera_clip is not None:
            near, far = map(float, camera_clip)
        if requested_ortho_scale is None:
            ortho_scale = scale_perspective_distance(
                ortho_scale,
                zoom=zoom,
                framing_margin=framing_margin,
            )
        remember_camera_fit(
            args,
            mode=fit_mode,
            target=target,
            points=fit_points,
            radii=fit_radii,
            includes_cell=fit_mode != "isosurface_content",
        )
        return CameraSpec(
            position=tuple(position),
            target=tuple(target),
            up=tuple(up),
            projection=args.projection,
            fov_y_deg=field_of_view,
            near=near,
            far=far,
            ortho_scale=ortho_scale,
        )

    from ..math.rotation import axis_camera_basis, largest_face_camera_axis

    content_view = content_direction_or_none(fit_mode, args, orientation_cloud)
    if args.view_direction is not None:
        direction = np.asarray(args.view_direction, dtype=float)
        default_up = matrix[1]
    elif content_view is not None:
        direction, default_up = content_view
    else:
        axis = args.camera_axis or largest_face_camera_axis(matrix)
        basis = axis_camera_basis(matrix, axis)
        direction = basis[2]
        default_up = basis[1]
    direction_norm = float(np.linalg.norm(direction))
    if direction_norm < 1.0e-12:
        raise ValueError("direction must be non-zero")
    up = np.array(
        args.camera_up if args.camera_up is not None else default_up,
        dtype=float,
        copy=True,
    )
    if np.linalg.norm(np.cross(direction, up)) < 1e-10:
        up = np.array([0.0, 1.0, 0.0])
        if np.linalg.norm(np.cross(direction, up)) < 1e-10:
            up = np.array([1.0, 0.0, 0.0])
    up /= np.linalg.norm(up)
    if args.projection == "orthographic" and requested_ortho_scale is None:
        ortho_scale = orthographic_scale_for_points(
            fit_points,
            fit_radii,
            target,
            direction / direction_norm,
            up,
            aspect=aspect,
            zoom=zoom,
            framing_margin=framing_margin,
        )
    multiplier = float(args.camera_distance)
    if not np.isfinite(multiplier) or multiplier <= 0.0:
        raise ValueError("--camera-distance fit multiplier must be finite and positive")
    if args.projection == "perspective":
        half_fov = np.radians(field_of_view * 0.5)
        distance = scale_perspective_distance(
            multiplier * radius / np.sin(half_fov),
            zoom=zoom,
            framing_margin=framing_margin,
        )
    else:
        distance = max(multiplier * radius, 1.5 * radius)
    near = max(radius * 1.0e-4, distance - 1.5 * radius)
    far = distance + 1.5 * radius
    if camera_clip is not None:
        near, far = map(float, camera_clip)
    remember_camera_fit(
        args,
        mode=fit_mode,
        target=target,
        points=fit_points,
        radii=fit_radii,
        includes_cell=fit_mode != "isosurface_content"
        and (display == "unit_cell" or _effective_show_cell(structure, args)),
    )
    return CameraSpec.looking_along(
        direction,
        target=target,
        up=up,
        distance=distance,
        projection=args.projection,
        ortho_scale=ortho_scale,
        fov_y_deg=field_of_view,
        near=near,
        far=far,
    )


