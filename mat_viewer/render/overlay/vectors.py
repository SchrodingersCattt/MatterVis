"""Native world-space vector overlays for Plotly 3D scenes."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from ...compass import camera_screen_basis
from ..geometry import arrow_primitive

MAGNITUDE_MODES = {"absolute", "scaled", "normalized"}
VIEWPORT_POLICIES = {"include", "clip"}


def _vec3(raw: Any, name: str) -> np.ndarray:
    try:
        vector = np.asarray(raw, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain three numbers") from exc
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must contain three finite numbers")
    return vector


def _cartesian(
    vector: np.ndarray, space: str, lattice: np.ndarray | None, name: str
) -> np.ndarray:
    if space == "cartesian":
        return vector
    if space != "fractional":
        raise ValueError(f"{name} space must be 'cartesian' or 'fractional'")
    if lattice is None or lattice.shape != (3, 3):
        raise ValueError(
            f"{name} uses fractional coordinates but no 3x3 lattice was supplied"
        )
    return vector @ lattice


def normalize_vector_overlays(raw: Any) -> list[dict]:
    """Return JSON-safe vector groups with stable IDs and validated policies."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("vector_overlays must be a list")
    groups: list[dict] = []
    group_ids: set[str] = set()
    for group_index, raw_group in enumerate(raw):
        if not isinstance(raw_group, dict):
            raise ValueError(f"vector overlay group {group_index} must be a dict")
        group = dict(raw_group)
        group_id = str(group.get("id") or f"vectors_{group_index}").strip()
        if not group_id or group_id in group_ids:
            raise ValueError(f"duplicate or empty vector group id: {group_id!r}")
        group_ids.add(group_id)
        mode = str(group.get("magnitude_mode") or "").strip()
        if mode not in MAGNITUDE_MODES:
            raise ValueError(
                "magnitude_mode must be explicitly set to absolute, scaled, or normalized"
            )
        if mode == "scaled" and "scale" not in group:
            raise ValueError(f"scaled vector group {group_id!r} requires scale")
        if mode == "normalized" and "length" not in group:
            raise ValueError(f"normalized vector group {group_id!r} requires length")
        viewport_policy = str(group.get("viewport_policy") or "include")
        if viewport_policy not in VIEWPORT_POLICIES:
            raise ValueError("viewport_policy must be include or clip")
        style = dict(group.get("style") or {})
        arrows_raw = group.get("arrows") or []
        if not isinstance(arrows_raw, list):
            raise ValueError(f"arrows in group {group_id!r} must be a list")
        arrows = []
        arrow_ids: set[str] = set()
        for arrow_index, raw_arrow in enumerate(arrows_raw):
            if not isinstance(raw_arrow, dict):
                raise ValueError(
                    f"arrow {arrow_index} in group {group_id!r} must be a dict"
                )
            arrow = dict(raw_arrow)
            arrow_id = str(arrow.get("id") or f"arrow_{arrow_index}").strip()
            if not arrow_id or arrow_id in arrow_ids:
                raise ValueError(
                    f"duplicate or empty arrow id in group {group_id!r}: {arrow_id!r}"
                )
            arrow_ids.add(arrow_id)
            if ("vector" in arrow) == ("end" in arrow):
                raise ValueError(
                    f"arrow {arrow_id!r} must define exactly one of vector or end"
                )
            arrow["origin"] = _vec3(arrow.get("origin"), "origin").tolist()
            if "vector" in arrow:
                arrow["vector"] = _vec3(arrow["vector"], "vector").tolist()
            else:
                arrow["end"] = _vec3(arrow["end"], "end").tolist()
            arrow["id"] = arrow_id
            arrow["visible"] = bool(arrow.get("visible", True))
            if "tail_offset" in arrow:
                arrow["tail_offset"] = float(arrow["tail_offset"])
                if not np.isfinite(arrow["tail_offset"]):
                    raise ValueError("tail_offset must be finite")
            arrows.append(arrow)
        group.update(
            {
                "id": group_id,
                "name": str(group.get("name") or group_id),
                "visible": bool(group.get("visible", True)),
                "magnitude_mode": mode,
                "viewport_policy": viewport_policy,
                "opacity": float(group.get("opacity", 1.0)),
                "style": style,
                "arrows": arrows,
            }
        )
        if not 0.0 < group["opacity"] <= 1.0:
            raise ValueError("vector overlay opacity must lie in (0, 1]")
        groups.append(group)
    return groups


def vector_overlays_in_scene_frame(
    vector_overlays: Any,
    scene: dict | Any,
) -> Any:
    """Translate source-Cartesian overlay positions into a synthetic scene cell."""
    if not bool(scene.get("synthetic_cell", False)):
        return vector_overlays
    origin_shift = np.asarray(scene.get("origin_shift", (0.0, 0.0, 0.0)), dtype=float)
    if origin_shift.shape != (3,) or not np.all(np.isfinite(origin_shift)):
        raise ValueError(
            "synthetic-cell origin_shift must contain three finite numbers"
        )
    if np.allclose(origin_shift, 0.0):
        return vector_overlays

    translated = normalize_vector_overlays(vector_overlays)
    offset = -origin_shift
    for group in translated:
        for arrow in group["arrows"]:
            if str(arrow.get("origin_space") or "cartesian") == "cartesian":
                arrow["origin"] = (
                    np.asarray(arrow["origin"], dtype=float) + offset
                ).tolist()
            if (
                "end" in arrow
                and str(arrow.get("end_space") or "cartesian") == "cartesian"
            ):
                arrow["end"] = (np.asarray(arrow["end"], dtype=float) + offset).tolist()
    return translated


def attach_vector_overlays(scene: dict, vector_overlays: Any) -> None:
    """Attach optional source-frame vectors to a normalized scene."""
    if vector_overlays is not None:
        scene["vector_overlays"] = vector_overlays_in_scene_frame(
            vector_overlays, scene
        )


def resolve_vector_overlays(raw: Any, *, lattice=None) -> list[dict]:
    """Resolve coordinate spaces and magnitude policies to Cartesian arrows."""
    groups = normalize_vector_overlays(raw)
    lattice_array = np.asarray(lattice, dtype=float) if lattice is not None else None
    resolved = []
    for group in groups:
        if not group["visible"]:
            continue
        mode = group["magnitude_mode"]
        group_style = dict(group["style"])
        for arrow in group["arrows"]:
            if not arrow["visible"]:
                continue
            style = {**group_style, **dict(arrow.get("style") or {})}
            origin = _cartesian(
                np.asarray(arrow["origin"], dtype=float),
                str(arrow.get("origin_space") or "cartesian"),
                lattice_array,
                "origin",
            )
            if "vector" in arrow:
                raw_vector = _cartesian(
                    np.asarray(arrow["vector"], dtype=float),
                    str(arrow.get("direction_space") or "cartesian"),
                    lattice_array,
                    "vector",
                )
            else:
                endpoint = _cartesian(
                    np.asarray(arrow["end"], dtype=float),
                    str(arrow.get("end_space") or "cartesian"),
                    lattice_array,
                    "end",
                )
                raw_vector = endpoint - origin
            magnitude = float(np.linalg.norm(raw_vector))
            if magnitude <= 1.0e-10:
                if str(group.get("zero_policy") or "error") == "skip":
                    continue
                raise ValueError(f"zero-length vector in {group['id']}/{arrow['id']}")
            if mode == "absolute":
                display_vector = raw_vector
            elif mode == "scaled":
                scale = float(arrow.get("scale", group.get("scale")))
                if not np.isfinite(scale) or scale <= 0.0:
                    raise ValueError("scaled vectors require a positive finite scale")
                display_vector = scale * raw_vector
            else:
                length = float(arrow.get("length", group.get("length")))
                if not np.isfinite(length) or length <= 0.0:
                    raise ValueError(
                        "normalized vectors require a positive finite length"
                    )
                display_vector = length * raw_vector / magnitude
            direction = display_vector / np.linalg.norm(display_vector)
            origin = origin + float(arrow.get("tail_offset", 0.0)) * direction
            end = origin + display_vector
            resolved.append(
                {
                    "group_id": group["id"],
                    "group_name": group["name"],
                    "arrow_id": arrow["id"],
                    "origin": origin,
                    "end": end,
                    "display_vector": display_vector,
                    "raw_magnitude": magnitude,
                    "display_magnitude": float(np.linalg.norm(display_vector)),
                    "color": str(arrow.get("color") or group.get("color") or "#D55E00"),
                    "opacity": float(arrow.get("opacity", group["opacity"])),
                    "label": arrow.get("label"),
                    "metadata": dict(arrow.get("metadata") or {}),
                    "viewport_policy": group["viewport_policy"],
                    "style": style,
                }
            )
    return resolved


def vector_primitives(vector_overlays: Any, *, lattice=None) -> list:
    """Compile resolved vector overlays into backend-neutral primitives."""
    results = []
    for arrow in resolve_vector_overlays(vector_overlays, lattice=lattice):
        style = arrow["style"]
        results.append(
            arrow_primitive(
                f"vector:{arrow['group_id']}:{arrow['arrow_id']}",
                arrow["origin"],
                arrow["end"],
                arrow["color"],
                shaft_radius=float(style.get("shaft_radius", 0.08)),
                head_length=(
                    float(style["head_length"])
                    if style.get("head_length") is not None
                    else None
                ),
                head_radius_ratio=float(style.get("head_radius_ratio", 2.2)),
                head_length_ratio=float(style.get("head_length_ratio", 0.28)),
                head_radius=(
                    float(style["head_radius"])
                    if style.get("head_radius") is not None
                    else None
                ),
                sides=int(style.get("sides", 12)),
                alpha=arrow["opacity"],
                metadata={
                    "group_id": arrow["group_id"],
                    "group_name": arrow["group_name"],
                    "arrow_id": arrow["arrow_id"],
                    "raw_magnitude": arrow["raw_magnitude"],
                    "display_magnitude": arrow["display_magnitude"],
                    **arrow["metadata"],
                },
            )
        )
    return results


def _mesh_for_arrow(arrow: dict) -> tuple[np.ndarray, np.ndarray]:
    from ..meshes import arrow_mesh_geometry

    style = arrow["style"]
    return arrow_mesh_geometry(
        arrow["origin"],
        arrow["end"],
        shaft_radius=float(style.get("shaft_radius", 0.10)),
        head_length=style.get("head_length"),
        head_length_ratio=float(style.get("head_length_ratio", 0.28)),
        head_radius=style.get("head_radius"),
        head_radius_ratio=float(style.get("head_radius_ratio", 2.2)),
        sides=int(style.get("sides", 12)),
    )


def vector_mesh_traces(vector_overlays: Any, *, lattice=None) -> list[dict]:
    """Build opaque Mesh3d arrow traces, batched by group/color/material."""
    resolved = resolve_vector_overlays(vector_overlays, lattice=lattice)
    bins: dict[tuple, list[dict]] = defaultdict(list)
    for arrow in resolved:
        style = arrow["style"]
        lighting = style.get("lighting")
        lighting_key = (
            tuple(sorted(lighting.items())) if isinstance(lighting, dict) else None
        )
        key = (
            arrow["group_id"],
            arrow["color"],
            float(arrow["opacity"]),
            bool(style.get("flatshading", True)),
            lighting_key,
        )
        bins[key].append(arrow)
    traces = []
    for (group_id, color, opacity, flatshading, lighting_key), arrows in bins.items():
        vertices_list = []
        triangles_list = []
        customdata = []
        offset = 0
        item_metadata = {}
        for arrow in arrows:
            vertices, triangles = _mesh_for_arrow(arrow)
            vertices_list.append(vertices)
            triangles_list.append(triangles + offset)
            custom = [
                arrow["arrow_id"],
                group_id,
                str(arrow.get("label") or ""),
                arrow["raw_magnitude"],
                arrow["display_magnitude"],
            ]
            customdata.extend([custom] * len(vertices))
            item_metadata[arrow["arrow_id"]] = arrow["metadata"]
            offset += len(vertices)
        vertices = np.vstack(vertices_list)
        triangles = np.vstack(triangles_list)
        n_vertices = len(vertices)
        trace = {
            "type": "mesh3d",
            "x": np.asarray(vertices[:, 0], dtype=np.float32),
            "y": np.asarray(vertices[:, 1], dtype=np.float32),
            "z": np.asarray(vertices[:, 2], dtype=np.float32),
            "i": np.asarray(
                triangles[:, 0], dtype=np.int16 if n_vertices < 32768 else np.int32
            ),
            "j": np.asarray(
                triangles[:, 1], dtype=np.int16 if n_vertices < 32768 else np.int32
            ),
            "k": np.asarray(
                triangles[:, 2], dtype=np.int16 if n_vertices < 32768 else np.int32
            ),
            "color": color,
            "opacity": opacity,
            "flatshading": flatshading,
            "customdata": customdata,
            "hovertemplate": "%{customdata[2]}<br>raw=%{customdata[3]:.5g}<br>display=%{customdata[4]:.5g}<extra></extra>",
            "legendgroup": group_id,
            "name": arrows[0]["group_name"],
            "showlegend": False,
            "uid": f"vector-{group_id}-{color}-{opacity:g}",
            "meta": {
                "mv_role": "vector",
                "mv_group": group_id,
                "mv_items": item_metadata,
            },
        }
        if lighting_key is not None:
            trace["lighting"] = dict(lighting_key)
        traces.append(trace)
    return traces


def vector_overlay_bounds(
    vector_overlays: Any, *, lattice=None
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Return min/max generated mesh vertices for groups with include policy."""
    points = []
    for arrow in resolve_vector_overlays(vector_overlays, lattice=lattice):
        if arrow["viewport_policy"] != "include":
            continue
        vertices, _ = _mesh_for_arrow(arrow)
        points.append(vertices)
    if not points:
        return None, None
    vertices = np.vstack(points)
    return vertices.min(axis=0), vertices.max(axis=0)


def _orthographic_project_point(
    point,
    *,
    camera: dict,
    ranges,
    domain,
    cube_scale=None,
) -> tuple[float, float]:
    right, screen_up = camera_screen_basis(camera)
    center = np.asarray([(axis[0] + axis[1]) / 2.0 for axis in ranges], dtype=float)
    spans = np.asarray([axis[1] - axis[0] for axis in ranges], dtype=float)
    scale = (
        np.asarray(cube_scale, dtype=float)
        if cube_scale is not None
        else np.full(3, float(spans.max()) / 2.0, dtype=float)
    )
    if scale.shape != (3,) or not np.all(np.isfinite(scale)) or np.any(scale <= 0.0):
        raise ValueError("cube_scale must contain three positive finite values")
    cube_point = (np.asarray(point, dtype=float) - center) / scale
    projected = np.asarray([cube_point @ right, cube_point @ screen_up])
    x0, x1, y0, y1 = domain
    return (
        float((x0 + x1) / 2.0 + projected[0] * (x1 - x0) / 2.0),
        float((y0 + y1) / 2.0 + projected[1] * (y1 - y0) / 2.0),
    )


def paper_vector_label_annotations(
    vector_overlays: Any,
    *,
    lattice=None,
    camera: dict,
    ranges,
    domain=(0.0, 1.0, 0.0, 1.0),
    cube_scale=None,
    pixel_offset=(7, 7),
    font_size: int = 13,
    font_family: str = "Arial, Helvetica, sans-serif",
) -> list[dict]:
    """Project arrow-tip labels to paper coordinates for static orthographic views."""
    projection = camera.get("projection") or {}
    projection_type = (
        projection.get("type") if isinstance(projection, dict) else projection
    )
    if str(projection_type or "perspective") != "orthographic":
        raise ValueError(
            "paper vector labels currently require orthographic projection"
        )
    annotations = []
    for arrow in resolve_vector_overlays(vector_overlays, lattice=lattice):
        label = arrow.get("label")
        if not label:
            continue
        x, y = _orthographic_project_point(
            arrow["end"],
            camera=camera,
            ranges=ranges,
            domain=domain,
            cube_scale=cube_scale,
        )
        annotations.append(
            {
                "x": x,
                "y": y,
                "xref": "paper",
                "yref": "paper",
                "xshift": int(pixel_offset[0]),
                "yshift": int(pixel_offset[1]),
                "showarrow": False,
                "text": str(label),
                "font": {
                    "size": int(font_size),
                    "family": font_family,
                    "color": arrow["color"],
                },
                "meta": {
                    "mv_role": "vector_label",
                    "mv_group": arrow["group_id"],
                    "mv_item": arrow["arrow_id"],
                },
            }
        )
    return annotations


__all__ = [
    "MAGNITUDE_MODES",
    "VIEWPORT_POLICIES",
    "normalize_vector_overlays",
    "resolve_vector_overlays",
    "vector_primitives",
    "vector_mesh_traces",
    "vector_overlay_bounds",
    "paper_vector_label_annotations",
]
