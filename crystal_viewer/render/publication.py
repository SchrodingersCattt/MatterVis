"""Deterministic Matplotlib compositor for dense coordination-polyhedron figures.

Interactive HTML keeps the Plotly renderer. Static PNG/PDF/SVG uses this
compositor so transparent faces, mixed sites, panel cameras, and exact figure
geometry are reproducible without a browser.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgba
from matplotlib.patches import Circle, FancyBboxPatch, Wedge
from mpl_toolkits.mplot3d import proj3d
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection
from scipy.spatial import ConvexHull

from .publication_geometry import (
    filter_polyhedra_to_half_open_cell,
    in_half_open_cell,
)
from .publication_materials import (
    _axis_camera_basis,
    _polyhedron_facecolors,
    _sphere_facecolors,
)
from .publication_style import _deep_merge, publication_config
from .topology import _hull_edges, _hull_simplices, representative_polyhedron_overlay


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


def _coordination_number(result: dict[str, Any]) -> int:
    overlay = representative_polyhedron_overlay(result)
    return len(overlay.get("shell_coords") or []) if overlay is not None else 0


def _material_for(
    config: dict[str, Any],
    result: dict[str, Any],
    coordination: int,
    role: str,
) -> dict[str, Any]:
    fallback_color = str(result.get("color") or "#7C5CBF")
    fallback = {
        "fill": fallback_color,
        "alpha": float(result.get("opacity", 0.55)),
        "edge": fallback_color,
        "edge_alpha": float(result.get("edge_opacity", 0.90)),
    }
    material = _deep_merge(
        fallback,
        dict(config.get("materials", {}).get(str(coordination), {}).get(role) or {}),
    )
    spec_override = dict(
        config.get("specs", {}).get(str(result.get("spec_id")), {}) or {}
    )
    return _deep_merge(
        material,
        dict(spec_override.get(f"{role}_material") or {}),
    )


def _draw_main_polyhedra(
    ax: Any,
    results: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, int]:
    faces: list[np.ndarray] = []
    facecolors: list[Any] = []
    face_edgecolors: list[Any] = []
    face_light_strengths: list[float] = []
    spokes: list[np.ndarray] = []
    front_edge_faces = 0
    back_edge_faces = 0
    counts: dict[str, int] = {}
    lines = config["lines"]
    basis = _axis_camera_basis(ax)
    for result in results:
        coordination = _coordination_number(result)
        material = _material_for(config, result, coordination, "main")
        item_faces, _, item_spokes = _polyhedron_geometry(result["overlays"])
        front_mask = _front_face_mask(result["overlays"], basis[2])
        if len(front_mask) != len(item_faces):
            raise ValueError("front-face mask does not match polyhedron faces")
        faces.extend(item_faces)
        facecolors.extend(
            [to_rgba(material["fill"], float(material["alpha"]))] * len(item_faces)
        )
        face_light_strengths.extend(
            [float(material.get("light_strength", 1.0))] * len(item_faces)
        )
        face_edgecolors.extend(
            [
                to_rgba(
                    material["edge"],
                    float(material["edge_alpha"]) if is_front else 0.0,
                )
                for is_front in front_mask
            ]
        )
        front_edge_faces += sum(front_mask)
        back_edge_faces += len(front_mask) - sum(front_mask)
        spokes.extend(item_spokes)
        counts[str(result.get("spec_id") or coordination)] = len(result["overlays"])

    lighting = config["lighting"]
    main_edge_width = float(lines["main_edge_width"])
    face_collection = Poly3DCollection(
        faces,
        facecolors=_polyhedron_facecolors(
            faces,
            facecolors,
            basis=basis,
            ambient=float(lighting["polyhedron_ambient"]),
            diffuse=float(lighting["polyhedron_diffuse"]),
            strengths=face_light_strengths,
        ),
        edgecolors=face_edgecolors if main_edge_width > 0 else "none",
        linewidths=main_edge_width,
        shade=False,
        zsort="average",
    )
    setattr(face_collection, "_mattervis_role", "polyhedron_face_stack")
    setattr(face_collection, "_mattervis_front_edge_faces", front_edge_faces)
    setattr(face_collection, "_mattervis_back_edge_faces", back_edge_faces)
    ax.add_collection3d(face_collection)

    main_spoke_width = float(lines["main_spoke_width"])
    main_spoke_alpha = float(lines["main_spoke_alpha"])
    if spokes and main_spoke_width > 0 and main_spoke_alpha > 0:
        spoke_collection = Line3DCollection(
            spokes,
            colors=to_rgba(lines["spoke_color"], main_spoke_alpha),
            linewidths=main_spoke_width,
        )
        setattr(spoke_collection, "_mattervis_role", "main_polyhedron_spokes")
        ax.add_collection3d(spoke_collection)
    return counts


def _set_camera(ax: Any, points: Any, profile: dict[str, Any]) -> None:
    points_array = np.asarray(points, dtype=float)
    lower = points_array.min(axis=0)
    upper = points_array.max(axis=0)
    center = (lower + upper) / 2
    half = max(float((upper - lower).max()) / 2, 1.0) * 1.02
    ax.set_xlim(center[0] - half, center[0] + half)
    ax.set_ylim(center[1] - half, center[1] + half)
    ax.set_zlim(center[2] - half, center[2] + half)
    ax.set_box_aspect((1, 1, 1), zoom=float(profile.get("zoom", 1.0)))
    projection = str(profile.get("projection", "persp"))
    if projection == "persp":
        ax.set_proj_type(
            "persp",
            focal_length=float(profile.get("focal_length", 1.0)),
        )
    else:
        ax.set_proj_type("ortho")
    angles = profile.get("camera", {}).get("angles") or [30.0, -60.0, 0.0]
    ax.view_init(*[float(value) for value in angles])
    ax.set_axis_off()
    ax.set_facecolor(str(profile.get("background", "#FFFFFF")))


def _group_half_open_atoms(
    scene: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[float, float, float], list[dict[str, Any]]] = defaultdict(list)
    for atom in scene.get("draw_atoms") or []:
        cart = atom.get("cart")
        if not in_half_open_cell(scene, cart):
            continue
        key = tuple(round(float(value), 6) for value in np.asarray(cart, dtype=float))
        grouped[key].append(atom)
    return list(grouped.values())


def _site_style(
    atoms: list[dict[str, Any]],
    site_styles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    elements = {str(atom.get("elem") or "") for atom in atoms}
    return next(
        (
            style
            for style in site_styles
            if elements.intersection(str(item) for item in style.get("elements") or [])
        ),
        None,
    )


def _site_colors(
    atoms: list[dict[str, Any]],
    site_style: dict[str, Any] | None,
) -> tuple[list[str], list[float]]:
    if site_style and site_style.get("colors"):
        colors = [str(color) for color in site_style["colors"]]
        weights = [float(value) for value in site_style.get("weights") or []]
        if len(weights) != len(colors):
            weights = [1.0] * len(colors)
        return colors, weights
    unique: dict[str, tuple[str, float]] = {}
    for atom in atoms:
        element = str(atom.get("elem") or "")
        if element not in unique:
            unique[element] = (
                str(atom.get("_render_color") or atom.get("color") or "#808080"),
                float(atom.get("occ", 1.0)),
            )
    return (
        [value[0] for value in unique.values()],
        [value[1] for value in unique.values()],
    )


def _main_center_groups(
    scene: dict[str, Any],
    results: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[tuple[np.ndarray, float, list[str], list[float]]]:
    center_species = {str(result.get("center_species") or "") for result in results}
    site_styles = list(config.get("site_styles") or [])
    groups = []
    for atoms in _group_half_open_atoms(scene):
        style = _site_style(atoms, site_styles)
        elements = {str(atom.get("elem") or "") for atom in atoms}
        if style is None and not elements.intersection(center_species):
            continue
        colors, weights = _site_colors(atoms, style)
        radius = float(
            (style or {}).get(
                "radius",
                config["atoms"]["center_radius_default"],
            )
        )
        groups.append(
            (
                np.asarray(atoms[0]["cart"], dtype=float),
                radius,
                colors,
                weights,
            )
        )
    return groups


def _ligand_color(
    scene: dict[str, Any],
    results: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    explicit = config.get("atoms", {}).get("ligand_color")
    if explicit:
        return str(explicit)
    ligands = {str(result.get("ligand_species") or "") for result in results}
    return next(
        (
            str(atom.get("_render_color") or atom.get("color"))
            for atom in scene.get("draw_atoms") or []
            if str(atom.get("elem") or "") in ligands and atom.get("color")
        ),
        "#E00000",
    )


def _orient_panel(
    overlay: dict[str, Any],
    profile: dict[str, Any],
) -> np.ndarray:
    center = np.asarray(overlay["center_coords"], dtype=float)
    shell = np.asarray(overlay["shell_coords"], dtype=float) - center
    mode = str(profile.get("orientation", "raw"))
    if mode == "first_two" and len(shell) >= 2:
        eye = shell[0] / max(float(np.linalg.norm(shell[0])), 1e-12)
        up = shell[1] - eye * float(np.dot(shell[1], eye))
        up /= max(float(np.linalg.norm(up)), 1e-12)
        right = np.cross(up, eye)
        right /= max(float(np.linalg.norm(right)), 1e-12)
        shell = np.column_stack((shell @ right, shell @ up, shell @ eye))
    elif mode == "first_face" and len(shell) >= 4:
        simplices = _hull_simplices(shell, overlay.get("hull") or {})
        if len(simplices):
            first, second, third = shell[simplices[0]]
            eye = np.cross(second - first, third - first)
            eye /= max(float(np.linalg.norm(eye)), 1e-12)
            up = first - eye * float(np.dot(first, eye))
            up /= max(float(np.linalg.norm(up)), 1e-12)
            right = np.cross(up, eye)
            right /= max(float(np.linalg.norm(right)), 1e-12)
            theta = np.deg2rad(float(profile.get("in_plane_rotation", 0.0)))
            right, up = (
                np.cos(theta) * right + np.sin(theta) * up,
                -np.sin(theta) * right + np.cos(theta) * up,
            )
            shell = np.column_stack((shell @ right, shell @ up, shell @ eye))
    return shell


def _panel_profile(
    config: dict[str, Any],
    result: dict[str, Any],
    coordination: int,
) -> dict[str, Any]:
    base = dict(
        config.get("panels", {}).get("by_coordination", {}).get(str(coordination)) or {}
    )
    override = dict(config.get("specs", {}).get(str(result.get("spec_id")), {}) or {})
    return _deep_merge(base, dict(override.get("panel") or {}))


def _panel_center_style(
    scene: dict[str, Any],
    result: dict[str, Any],
    config: dict[str, Any],
) -> tuple[list[str], list[float], float]:
    center_species = str(result.get("center_species") or "")
    group = next(
        (
            atoms
            for atoms in _group_half_open_atoms(scene)
            if center_species in {str(atom.get("elem") or "") for atom in atoms}
        ),
        None,
    )
    if group is None:
        group = [
            {
                "elem": center_species,
                "color": result.get("center_color", "#808080"),
            }
        ]
    site_style = _site_style(group, list(config.get("site_styles") or []))
    colors, weights = _site_colors(group, site_style)
    coordination = _coordination_number(result)
    radius = float(
        (site_style or {}).get(
            "radius",
            config["panels"]["center_radius"].get(
                str(coordination),
                config["atoms"]["center_radius_default"],
            ),
        )
    )
    return colors, weights, radius


def _draw_panel(
    ax: Any,
    scene: dict[str, Any],
    result: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, int]:
    overlay = representative_polyhedron_overlay(result)
    if overlay is None:
        return {"front_ligands": 0, "back_ligands": 0}
    coordination = len(overlay.get("shell_coords") or [])
    profile = _panel_profile(config, result, coordination)
    shell = _orient_panel(overlay, profile)
    shifted = {
        **overlay,
        "center_coords": [0.0, 0.0, 0.0],
        "shell_coords": shell.tolist(),
    }
    _set_camera(
        ax,
        np.vstack((np.zeros((1, 3)), shell)),
        {**profile, "background": config["background"]},
    )
    basis = _axis_camera_basis(ax)
    ax.computed_zorder = False
    x2d, y2d, z2d = proj3d.proj_transform(
        shell[:, 0],
        shell[:, 1],
        shell[:, 2],
        ax.get_proj(),
    )
    try:
        front = set(ConvexHull(np.column_stack((x2d, y2d))).vertices.tolist())
    except Exception:
        front = set(range(len(shell)))
    depth_span = float(np.ptp(z2d))
    far_depth = {
        index
        for index, depth in enumerate(z2d)
        if depth < float(np.median(z2d)) - 0.25 * depth_span
    }
    front -= far_depth
    back = set(range(len(shell))) - front

    center_colors, center_weights, center_radius = _panel_center_style(
        scene,
        result,
        config,
    )
    detail = tuple(int(value) for value in config["atoms"]["sphere_detail_panel"])
    gloss_color = str(config["atoms"]["gloss_color"])
    sphere_ambient = float(config["atoms"].get("sphere_ambient", 0.72))
    sphere_diffuse = float(config["atoms"].get("sphere_diffuse", 0.28))
    sphere_clip_on = bool(config["atoms"].get("sphere_clip_on", True))
    _draw_sphere(
        ax,
        np.zeros(3),
        center_radius,
        center_colors,
        weights=center_weights,
        basis=basis,
        detail=detail,
        alpha=float(config["panels"]["center_alpha"]),
        zorder=0.0,
        gloss_color=gloss_color,
        ambient=sphere_ambient,
        diffuse=sphere_diffuse,
        clip_on=sphere_clip_on,
    )
    ligand_color = _ligand_color(scene, [result], config)
    for index in sorted(back):
        _draw_sphere(
            ax,
            shell[index],
            float(config["panels"]["ligand_radius_back"]),
            [ligand_color],
            basis=basis,
            detail=detail,
            zorder=0.1,
            gloss_color=gloss_color,
            ambient=sphere_ambient,
            diffuse=sphere_diffuse,
            clip_on=sphere_clip_on,
        )

    material = _material_for(config, result, coordination, "panel")
    faces, _, spokes = _polyhedron_geometry([shifted])
    front_edges, rear_edges = _split_hull_edges_by_facing(shifted, basis[2])
    lines = config["lines"]
    edge_color = to_rgba(material["edge"], float(material["edge_alpha"]))

    if rear_edges:
        ax.add_collection3d(
            Line3DCollection(
                rear_edges,
                colors=edge_color,
                linewidths=float(lines["panel_edge_width"]),
                zorder=0.35,
            )
        )
    if spokes:
        ax.add_collection3d(
            Line3DCollection(
                spokes,
                colors=to_rgba(
                    lines["spoke_color"],
                    float(lines["panel_spoke_alpha"]),
                ),
                linewidths=float(lines["panel_spoke_width"]),
                zorder=0.45,
            )
        )

    panel_facecolors = [
        to_rgba(material["fill"], float(material["alpha"])) for _ in faces
    ]
    ax.add_collection3d(
        Poly3DCollection(
            faces,
            facecolors=_polyhedron_facecolors(
                faces,
                panel_facecolors,
                basis=basis,
                ambient=float(config["lighting"]["polyhedron_ambient"]),
                diffuse=float(config["lighting"]["polyhedron_diffuse"]),
                strengths=[float(material.get("light_strength", 1.0))] * len(faces),
            ),
            edgecolors="none",
            linewidths=0.0,
            shade=False,
            zsort="average",
            zorder=1.0,
        )
    )
    if front_edges:
        ax.add_collection3d(
            Line3DCollection(
                front_edges,
                colors=edge_color,
                linewidths=float(lines["panel_edge_width"]),
                zorder=1.1,
            )
        )
    for index in sorted(front):
        _draw_sphere(
            ax,
            shell[index],
            float(config["panels"]["ligand_radius_front"]),
            [ligand_color],
            basis=basis,
            detail=detail,
            zorder=3.0,
            gloss_color=gloss_color,
            ambient=sphere_ambient,
            diffuse=sphere_diffuse,
            clip_on=sphere_clip_on,
        )
    return {
        "front_ligands": len(front),
        "back_ligands": len(back),
        "front_edges": len(front_edges),
        "back_edges": len(rear_edges),
        "interior_spokes": len(spokes),
    }


def _panel_rects(count: int, layout: dict[str, Any]) -> list[list[float]]:
    if count <= 0:
        return []
    left = float(layout["left"])
    right = float(layout["right"])
    gap = float(layout["gap"])
    width = (1.0 - left - right - gap * (count - 1)) / count
    return [
        [
            left + index * (width + gap),
            float(layout["bottom"]),
            width,
            float(layout["height"]),
        ]
        for index in range(count)
    ]


def _draw_legend_icon(
    fig: Any,
    *,
    x: float,
    y: float,
    height: float,
    colors: list[str],
    weights: list[float] | None,
) -> None:
    width = height * fig.get_figheight() / fig.get_figwidth()
    ax = fig.add_axes(
        [x - width / 2, y - height / 2, width, height],
        frameon=False,
    )
    ax.set_xlim(-1.05, 1.05)
    ax.set_ylim(-1.05, 1.05)
    ax.set_aspect("equal")
    ax.axis("off")
    clean_colors, fractions = _normalise_sectors(colors, weights)
    angle = 90.0
    for color, fraction in zip(clean_colors, fractions):
        next_angle = angle + 360.0 * float(fraction)
        ax.add_patch(
            Wedge(
                (0, 0),
                1,
                angle,
                next_angle,
                facecolor=color,
                edgecolor="none",
            )
        )
        angle = next_angle
    ax.add_patch(
        Circle(
            (0, 0),
            1,
            facecolor="none",
            edgecolor="#111111",
            linewidth=0.75,
        )
    )
    if len(clean_colors) == 2 and np.allclose(fractions, [0.5, 0.5]):
        ax.plot([0, 0], [-1, 1], color="#111111", linewidth=0.65)


def _legend_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = list(config["legend"].get("entries") or [])
    if explicit:
        return explicit
    entries = []
    for style in config.get("site_styles") or []:
        if style.get("label") and style.get("colors"):
            entries.append(
                {
                    "label": str(style["label"]),
                    "colors": list(style["colors"]),
                    "weights": list(style.get("weights") or []),
                }
            )
    return entries


def _draw_legend(fig: Any, config: dict[str, Any]) -> None:
    legend = config["legend"]
    entries = _legend_entries(config)
    if not entries:
        return
    rect = [float(value) for value in legend["rect"]]
    box = FancyBboxPatch(
        (rect[0], rect[1]),
        rect[2],
        rect[3],
        boxstyle="round,pad=0.008,rounding_size=0.010",
        transform=fig.transFigure,
        facecolor=config["background"],
        edgecolor="#222222",
        linewidth=0.8,
    )
    box.set_zorder(-10)
    fig.patches.append(box)
    fig.text(
        float(legend["title_x"]),
        float(legend["title_y"]),
        str(legend["title"]),
        ha="center",
        va="center",
        fontsize=float(legend["title_size"]),
        fontweight="bold",
    )
    rows = np.linspace(
        float(legend["row_start"]),
        float(legend["row_end"]),
        len(entries),
    )
    for y, entry in zip(rows, entries):
        colors = [str(color) for color in entry.get("colors") or ["#808080"]]
        weights = [float(value) for value in entry.get("weights") or []] or None
        _draw_legend_icon(
            fig,
            x=float(legend["icon_x"]),
            y=float(y),
            height=float(legend["icon_height"]),
            colors=colors,
            weights=weights,
        )
        fig.text(
            float(legend["text_x"]),
            float(y),
            str(entry.get("label") or ""),
            ha="left",
            va="center",
            fontsize=float(legend["text_size"]),
        )
    if legend.get("footer"):
        fig.text(
            float(legend["footer_x"]),
            float(legend["footer_y"]),
            str(legend["footer"]),
            ha="center",
            va="center",
            fontsize=float(legend["footer_size"]),
            color="#333333",
        )


def _draw_compass(
    fig: Any,
    scene: dict[str, Any],
    config: dict[str, Any],
    basis: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> None:
    compass = config["compass"]
    ax = fig.add_axes(
        [float(value) for value in compass["rect"]],
        frameon=False,
    )
    ax.set_xlim(-1.1, 1.1)
    ax.set_ylim(-1.1, 1.1)
    ax.set_aspect("equal")
    ax.axis("off")
    right, up, _ = basis
    vectors = np.column_stack((np.eye(3) @ right, np.eye(3) @ up))
    vectors /= max(
        float(np.linalg.norm(vectors, axis=1).max()),
        1e-12,
    )
    labels = list(scene.get("axis_labels") or ["a", "b", "c"])
    colors = list(compass["colors"])
    for label, vector, color in zip(labels, vectors, colors):
        ax.annotate(
            "",
            xy=vector,
            xytext=(0, 0),
            arrowprops={
                "arrowstyle": "->",
                "linewidth": float(compass["line_width"]),
                "color": color,
            },
        )
        ax.text(
            *(vector * 1.16),
            str(label),
            color=color,
            fontsize=float(compass["font_size"]),
            fontweight="bold",
            ha="center",
            va="center",
        )


def build_static_publication_figure(
    scene: dict[str, Any],
    style: dict[str, Any],
    topology_data: dict[str, Any],
    *,
    title: str | None = None,
    subtitle: str | None = None,
    width: int = 1600,
    height: int = 1100,
    dpi: int = 300,
) -> Any:
    """Compose a browser-independent dense coordination publication figure."""
    config = publication_config(style)
    results = filter_polyhedra_to_half_open_cell(scene, topology_data)
    if not results:
        raise ValueError("static publication layout has no canonical-cell polyhedra")

    fig = plt.figure(
        figsize=(float(width) / dpi, float(height) / dpi),
        dpi=dpi,
        facecolor=config["background"],
    )
    main_ax = fig.add_axes(config["main"]["rect"], projection="3d")
    main_points = [
        point
        for result in results
        for overlay in result["overlays"]
        for point in overlay.get("shell_coords") or []
    ]
    _set_camera(
        main_ax,
        main_points,
        {**config["main"], "background": config["background"]},
    )
    main_basis = _axis_camera_basis(main_ax)
    counts = _draw_main_polyhedra(main_ax, results, config)
    detail = tuple(int(value) for value in config["atoms"]["sphere_detail_main"])
    gloss_color = str(config["atoms"]["gloss_color"])
    sphere_ambient = float(config["atoms"].get("sphere_ambient", 0.72))
    sphere_diffuse = float(config["atoms"].get("sphere_diffuse", 0.28))
    sphere_clip_on = bool(config["atoms"].get("sphere_clip_on", True))
    for center, radius, colors, weights in _main_center_groups(
        scene,
        results,
        config,
    ):
        _draw_sphere(
            main_ax,
            center,
            radius,
            colors,
            weights=weights,
            basis=main_basis,
            detail=detail,
            gloss_color=gloss_color,
            ambient=sphere_ambient,
            diffuse=sphere_diffuse,
            clip_on=sphere_clip_on,
        )

    ligand_vertices = {
        tuple(round(float(value), 6) for value in point)
        for result in results
        for overlay in result["overlays"]
        for point in overlay.get("shell_coords") or []
    }
    ligand_color = _ligand_color(scene, results, config)
    for point in sorted(ligand_vertices):
        _draw_sphere(
            main_ax,
            np.asarray(point, dtype=float),
            float(config["atoms"]["ligand_radius_main"]),
            [ligand_color],
            basis=main_basis,
            detail=detail,
            gloss_color=gloss_color,
            ambient=sphere_ambient,
            diffuse=sphere_diffuse,
            clip_on=sphere_clip_on,
        )
    drawable = [
        result
        for result in results
        if representative_polyhedron_overlay(result) is not None
    ]
    rects = _panel_rects(len(drawable), config["panels"]["layout"])
    panel_layers: dict[str, dict[str, int]] = {}
    for result, default_rect in zip(drawable, rects):
        spec_id = str(result.get("spec_id") or "")
        override = dict(config.get("specs", {}).get(spec_id, {}) or {})
        rect = list(override.get("panel_rect") or default_rect)
        panel_ax = fig.add_axes(rect, projection="3d")
        panel_layers[spec_id] = _draw_panel(
            panel_ax,
            scene,
            result,
            config,
        )
        label = str(override.get("panel_label") or result.get("name") or spec_id)
        fig.text(
            rect[0] + rect[2] / 2,
            float(config["panels"]["layout"]["label_y"]),
            label,
            ha="center",
            va="center",
            fontsize=float(config["panel_labels"]["size"]),
            fontweight=str(config["panel_labels"]["weight"]),
        )

    title_style = config["title"]
    fig.text(
        float(title_style["x"]),
        float(title_style["y"]),
        str(
            title
            or scene.get("display_title")
            or scene.get("title")
            or scene.get("name")
            or ""
        ),
        ha="center",
        va="top",
        fontsize=float(title_style["size"]),
        fontweight=str(title_style["weight"]),
    )
    if subtitle:
        fig.text(
            float(title_style["x"]),
            float(title_style["y"]) - 0.035,
            str(subtitle),
            ha="center",
            va="top",
            fontsize=max(6.0, float(title_style["size"]) * 0.62),
            color="#555555",
        )
    _draw_legend(fig, config)
    _draw_compass(fig, scene, config, main_basis)
    setattr(
        fig,
        "_mattervis_publication",
        {
            "cell_polyhedron_counts": counts,
            "panel_layers": panel_layers,
            "ligand_vertex_count": len(ligand_vertices),
            "preset": "dense_coordination",
        },
    )
    return fig
