from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

import numpy as np

from .contracts import CameraSpec


def _geometry_entity_points(scene: dict) -> list[np.ndarray]:
    points: list[np.ndarray] = []
    entities = scene.get("geometry_entities")
    if entities is None or not isinstance(entities, Iterable) or isinstance(entities, (str, bytes, Mapping)):
        return points
    for entity in entities:
        if not isinstance(entity, Mapping) or not bool(entity.get("visible", True)):
            continue
        try:
            coords = np.asarray(entity.get("vertices"), dtype=float)
        except (TypeError, ValueError):
            continue
        if coords.ndim != 2 or coords.shape[1] != 3 or len(coords) == 0 or not np.all(np.isfinite(coords)):
            continue
        points.extend(np.array(row, dtype=float) for row in coords)
    return points


def uniform_viewport(
    scenes,
    *,
    style=None,
    padding=0.0,
    shared_center=False,
):
    """Stamp equal-scale viewports, optionally with one shared world centre."""
    scenes = list(scenes)
    if not scenes:
        return []

    radius_spans = []
    centroids = []
    for scene in scenes:
        scn_style = style if style is not None else scene.get("style") or {}
        atom_scale = float(scn_style.get("atom_scale", 1.0))
        atoms = scene.get("draw_atoms") or []
        atom_mins = atom_maxs = None
        if atoms:
            carts = np.array([atom["cart"] for atom in atoms], dtype=float)
            radii = (
                np.array(
                    [max(float(atom.get("atom_radius", 0.18)), 0.05) for atom in atoms],
                    dtype=float,
                )
                * atom_scale
            )
            atom_mins = (carts - radii[:, None]).min(axis=0)
            atom_maxs = (carts + radii[:, None]).max(axis=0)

        geometry_points = _geometry_entity_points(scene)
        if geometry_points:
            geometry_arr = np.asarray(geometry_points, dtype=float)
            geometry_mins = geometry_arr.min(axis=0)
            geometry_maxs = geometry_arr.max(axis=0)
            if atom_mins is None:
                atom_mins, atom_maxs = geometry_mins, geometry_maxs
            else:
                atom_mins = np.minimum(atom_mins, geometry_mins)
                atom_maxs = np.maximum(atom_maxs, geometry_maxs)

        if atom_mins is None or atom_maxs is None:
            radius_spans.append(1.0)
            centroids.append(np.zeros(3, dtype=float))
            continue
        radius_spans.append(float((atom_maxs - atom_mins).max()))
        centroids.append(0.5 * (atom_mins + atom_maxs))

    if shared_center:
        centers_array = np.asarray(centroids, dtype=float)
        spans_array = np.asarray(radius_spans, dtype=float)[:, None]
        global_min = np.min(centers_array - 0.5 * spans_array, axis=0)
        global_max = np.max(centers_array + 0.5 * spans_array, axis=0)
        common_center = 0.5 * (global_min + global_max)
        half = 0.5 * float(np.max(global_max - global_min)) + float(padding)
        centroids = [common_center for _ in scenes]
    else:
        half = 0.5 * max(radius_spans) + float(padding)

    viewports = []
    for scene, center in zip(scenes, centroids):
        viewport = {
            "x": [float(center[0] - half), float(center[0] + half)],
            "y": [float(center[1] - half), float(center[1] + half)],
            "z": [float(center[2] - half), float(center[2] + half)],
            "center": [float(center[0]), float(center[1]), float(center[2])],
            "half_span": float(half),
        }
        scene["viewport"] = viewport
        viewports.append(viewport)
    return viewports


def uniform_camera(
    plans,
    *,
    direction=None,
    up=None,
    padding=0.0,
):
    """Return per-plan cameras with one direction and orthographic scale.

    This is the backend-neutral counterpart of :func:`uniform_viewport` for
    CPU and Matplotlib rendering. Each returned camera remains centred on its
    own plan while all panels use the same world length per pixel.

    Parameters
    ----------
    plans
        Render plans whose fitted cameras define the panel centres and depth
        ranges.
    direction, up
        Optional shared camera direction and up vector. When omitted, the
        first plan's fitted orientation is used.
    padding
        Extra world-space half-height added to the largest fitted
        ``ortho_scale``.
    """
    plans = list(plans)
    if not plans:
        return []

    cameras = [plan.camera for plan in plans]
    shared_scale = max(float(camera.ortho_scale) for camera in cameras) + float(padding)
    if not math.isfinite(shared_scale) or shared_scale <= 0.0:
        raise ValueError("uniform camera scale must be positive and finite")

    first = cameras[0]
    if direction is None:
        direction_vector = np.asarray(first.position, dtype=float) - np.asarray(
            first.target, dtype=float
        )
    else:
        direction_vector = np.asarray(direction, dtype=float)
    if direction_vector.shape != (3,) or not np.all(np.isfinite(direction_vector)):
        raise ValueError("direction must be a finite three-dimensional vector")
    if float(np.linalg.norm(direction_vector)) <= 1.0e-12:
        raise ValueError("direction must be non-zero")

    up_vector = np.asarray(first.up if up is None else up, dtype=float)
    if up_vector.shape != (3,) or not np.all(np.isfinite(up_vector)):
        raise ValueError("up must be a finite three-dimensional vector")
    if float(np.linalg.norm(up_vector)) <= 1.0e-12:
        raise ValueError("up must be non-zero")

    out = []
    for camera in cameras:
        target = np.asarray(camera.target, dtype=float)
        distance = float(
            np.linalg.norm(np.asarray(camera.position, dtype=float) - target)
        )
        out.append(
            CameraSpec.looking_along(
                direction_vector,
                target=target,
                up=up_vector,
                distance=distance,
                projection="orthographic",
                ortho_scale=shared_scale,
                near=camera.near,
                far=camera.far,
            )
        )
    return out
