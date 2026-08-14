"""Drawing primitives for static publication figures."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..topology import _hull_edges, _hull_simplices
from .materials import _sphere_facecolors


def _sphere_surface(
    center: np.ndarray,
    radius: float,
    *,
    angle_start: float,
    angle_end: float,
    nu: int,
    nv: int,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> np.ndarray:
    right, up, eye = basis
    u = np.linspace(angle_start, angle_end, nu)
    v = np.linspace(0.0, np.pi, nv)
    uu, vv = np.meshgrid(u, v)
    local = (
        np.sin(vv)[..., None] * np.cos(uu)[..., None] * right
        + np.sin(vv)[..., None] * np.sin(uu)[..., None] * eye
        + np.cos(vv)[..., None] * up
    )
    return center[None, None, :] + radius * local


def _normalise_sectors(
    colors: list[str] | tuple[str, ...],
    weights: list[float] | tuple[float, ...] | None,
) -> tuple[list[str], np.ndarray]:
    clean_colors = [str(color) for color in colors if color]
    if not clean_colors:
        clean_colors = ["#808080"]
    raw = np.asarray(
        weights if weights is not None else np.ones(len(clean_colors)),
        dtype=float,
    )
    if (
        raw.shape != (len(clean_colors),)
        or not np.all(np.isfinite(raw))
        or raw.sum() <= 0
    ):
        raw = np.ones(len(clean_colors), dtype=float)
    return clean_colors, raw / raw.sum()


def _draw_sphere(
    ax: Any,
    center: Any,
    radius: float,
    colors: list[str] | tuple[str, ...],
    *,
    weights: list[float] | tuple[float, ...] | None = None,
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
    detail: tuple[int, int],
    alpha: float = 1.0,
    glossy: bool = True,
    zorder: float | None = None,
    gloss_color: str = "#FFF7F7",
    ambient: float = 0.72,
    diffuse: float = 0.28,
    clip_on: bool = True,
) -> list[Any]:
    clean_colors, fractions = _normalise_sectors(colors, weights)
    kwargs = {
        "linewidth": 0,
        "antialiased": True,
        "shade": False,
        "clip_on": bool(clip_on),
        "alpha": alpha,
    }
    if zorder is not None:
        kwargs["zorder"] = zorder
    artists = []
    angle = np.pi / 2
    for color, fraction in zip(clean_colors, fractions):
        next_angle = angle + 2 * np.pi * float(fraction)
        xyz = _sphere_surface(
            np.asarray(center, dtype=float),
            float(radius),
            angle_start=angle,
            angle_end=next_angle,
            nu=max(5, round(detail[0] * float(fraction)) + 1),
            nv=int(detail[1]),
            basis=basis,
        )
        artists.append(
            ax.plot_surface(
                xyz[..., 0],
                xyz[..., 1],
                xyz[..., 2],
                facecolors=_sphere_facecolors(
                    xyz,
                    np.asarray(center, dtype=float),
                    color,
                    basis=basis,
                    ambient=ambient,
                    diffuse=diffuse,
                ),
                **kwargs,
            )
        )
        angle = next_angle
    if glossy and alpha >= 0.8:
        right, up, eye = basis
        highlight = eye - 0.34 * right + 0.38 * up
        highlight /= max(float(np.linalg.norm(highlight)), 1e-12)
        highlight_center = np.asarray(center, dtype=float) + highlight * radius * 0.82
        xyz = _sphere_surface(
            highlight_center,
            radius * 0.16,
            angle_start=0.0,
            angle_end=2 * np.pi,
            nu=max(8, detail[0] // 2),
            nv=max(6, detail[1] // 2),
            basis=basis,
        )
        artists.append(
            ax.plot_surface(
                xyz[..., 0],
                xyz[..., 1],
                xyz[..., 2],
                color=gloss_color,
                linewidth=0,
                antialiased=True,
                shade=False,
                alpha=0.82,
                zorder=None if zorder is None else zorder + 0.1,
                clip_on=bool(clip_on),
            )
        )
    return artists


def _polyhedron_geometry(
    overlays: list[dict[str, Any]],
) -> tuple[list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
    faces: list[np.ndarray] = []
    edges: list[np.ndarray] = []
    spokes: list[np.ndarray] = []
    for overlay in overlays:
        shell = np.asarray(overlay.get("shell_coords") or [], dtype=float)
        center = np.asarray(overlay.get("center_coords"), dtype=float)
        if shell.ndim != 2 or shell.shape[1:] != (3,) or center.shape != (3,):
            continue
        hull = overlay.get("hull") or {}
        simplices = _hull_simplices(shell, hull)
        faces.extend(shell[simplex] for simplex in simplices)
        edges.extend(
            np.vstack((shell[first], shell[second]))
            for first, second in _hull_edges(shell, hull)
        )
        spokes.extend(np.vstack((center, point)) for point in shell)
    return faces, edges, spokes


def _front_face_mask(
    overlays: list[dict[str, Any]],
    view_direction: np.ndarray,
) -> list[bool]:
    """Mark hull triangles whose outward normal faces the camera."""
    eye = np.asarray(view_direction, dtype=float)
    eye /= max(float(np.linalg.norm(eye)), 1e-12)
    visible: list[bool] = []
    for overlay in overlays:
        shell = np.asarray(overlay.get("shell_coords") or [], dtype=float)
        center = np.asarray(overlay.get("center_coords"), dtype=float)
        if shell.ndim != 2 or shell.shape[1:] != (3,) or center.shape != (3,):
            continue
        for simplex in _hull_simplices(shell, overlay.get("hull") or {}):
            face = shell[simplex]
            normal = np.cross(face[1] - face[0], face[2] - face[0])
            normal /= max(float(np.linalg.norm(normal)), 1e-12)
            if float(np.dot(normal, face.mean(axis=0) - center)) < 0:
                normal = -normal
            visible.append(float(np.dot(normal, eye)) >= 0.0)
    return visible


def _split_hull_edges_by_facing(
    overlay: dict[str, Any],
    view_direction: np.ndarray,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Split convex-hull edges into camera-facing and rear collections."""
    shell = np.asarray(overlay.get("shell_coords") or [], dtype=float)
    center = np.asarray(overlay.get("center_coords"), dtype=float)
    if shell.ndim != 2 or shell.shape[1:] != (3,) or center.shape != (3,):
        return [], []

    hull = overlay.get("hull") or {}
    edge_keys = set(_hull_edges(shell, hull))
    front_by_edge = {edge: False for edge in edge_keys}
    simplices = _hull_simplices(shell, hull)
    face_mask = _front_face_mask([overlay], view_direction)
    for simplex, is_front in zip(simplices, face_mask):
        for first, second in (
            (simplex[0], simplex[1]),
            (simplex[1], simplex[2]),
            (simplex[0], simplex[2]),
        ):
            edge = tuple(sorted((int(first), int(second))))
            if edge in front_by_edge and is_front:
                front_by_edge[edge] = True

    eye = np.asarray(view_direction, dtype=float)
    eye /= max(float(np.linalg.norm(eye)), 1e-12)
    front: list[np.ndarray] = []
    rear: list[np.ndarray] = []
    for edge in sorted(edge_keys):
        segment = np.vstack((shell[edge[0]], shell[edge[1]]))
        is_front = front_by_edge[edge]
        if len(simplices) == 0:
            is_front = float(np.dot(segment.mean(axis=0) - center, eye)) >= 0.0
        (front if is_front else rear).append(segment)
    return front, rear
