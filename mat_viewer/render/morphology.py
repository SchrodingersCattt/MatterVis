from __future__ import annotations

import numpy as np

from .common import go

from .geometry import geometry_entity_traces, mesh_entity
from .picking import facet_instance_id


def _morphology_center(scene: dict) -> np.ndarray:
    """Cartesian centre of the unit cell, where the BFDH solid is placed.

    Mesh vertices are stored around the origin so the scale slider grows the
    habit about this point instead of about the lattice origin.
    """
    matrix = scene.get("M")
    if matrix is None:
        return np.zeros(3)
    try:
        lattice = np.asarray(matrix, dtype=float)
    except (TypeError, ValueError):
        return np.zeros(3)
    if lattice.shape != (3, 3) or not np.all(np.isfinite(lattice)):
        return np.zeros(3)
    return (lattice[0] + lattice[1] + lattice[2]) / 2.0


def morphology_world_points(scene: dict, style: dict | None = None) -> np.ndarray:
    """World-frame vertices of the visible BFDH mesh, matching the traces."""
    morph = scene.get("bfdh_morphology")
    if not isinstance(morph, dict) or not morph.get("enabled"):
        return np.zeros((0, 3))
    style = style or {}
    from .style.resolver import normalize_style_overrides

    overrides = normalize_style_overrides(style.get("style_overrides"))
    parent_override = overrides.get("bfdh-morphology") or overrides.get(
        "morphology:bfdh-morphology"
    )
    if isinstance(parent_override, dict) and parent_override.get("visible") is False:
        return np.zeros((0, 3))
    try:
        scale = float(morph.get("scale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    center = _morphology_center(scene)
    points: list[np.ndarray] = []
    for facet in morph.get("facets") or []:
        for tri in facet.get("triangles") or []:
            try:
                arr = np.asarray(tri, dtype=float)
            except (TypeError, ValueError):
                continue
            if arr.shape != (3, 3) or not np.all(np.isfinite(arr)):
                continue
            points.append(arr * scale + center)
    if not points:
        return np.zeros((0, 3))
    return np.concatenate(points, axis=0)


def _morphology_traces(
    scene: dict,
    style: dict,
    *,
    include_face_ids: bool = True,
) -> list[go.Trace]:
    """Build traces for BFDH morphology overlay."""
    morph = scene.get("bfdh_morphology")
    if not morph or not morph.get("enabled"):
        return []

    facets = morph.get("facets", [])
    if not facets:
        return []

    scale = morph.get("scale", 1.0)
    opacity = morph.get("opacity", 0.8)
    color = str(style.get("bfdh_morphology_color") or "#4f7cff")
    from .style.resolver import normalize_style_overrides, resolve_entity_style
    overrides = normalize_style_overrides(style.get("style_overrides"))
    parent_override = overrides.get("bfdh-morphology") or overrides.get("morphology:bfdh-morphology")
    if isinstance(parent_override, dict):
        if parent_override.get("visible") is False:
            return []
        color = str(parent_override.get("color", color))
        opacity = parent_override.get("opacity", opacity)

    # Center the morphology at the center of the unit cell. Vertices are
    # stored about the origin, so scaling stays centred on the structure.
    center_offset = _morphology_center(scene)

    traces = []

    # Build the Wulff surface through the same generic mesh-entity path used by
    # caller-supplied solids.  Facet overrides are materialised as separate
    # meshes rather than rgba entries in one ``facecolor`` array.  Plotly's
    # Mesh3d alpha handling has varied across releases; a transparent
    # ``rgba(0,0,0,0)`` face could therefore fall back to opaque black.  A
    # hidden facet is omitted entirely and an opacity override becomes the
    # trace opacity, which is deterministic in both WebGL and static export.
    groups: dict[tuple, dict[str, list]] = {}
    visible_facet_indices: set[int] = set()
    for facet_index, facet in enumerate(facets):
        miller = facet.get("miller", [0, 0, 0])
        facet_id = facet_instance_id(miller, facet_index, morphology_id="bfdh-morphology")
        facet_override = resolve_entity_style(
            {"kind": "facet", "id": facet_id, "parent_id": "morphology:bfdh-morphology"},
            kind_defaults={"facet": {"edge_width": 0, **((style.get("entity_defaults") or {}).get("facet") or {})}},
            rules=[{"selector": {"all": True}, "style": {"color": color, "opacity": opacity}}],
            overrides=overrides,
        )
        # Accept the historic bare parent ID as well.
        if parent_override:
            facet_override = {**parent_override, **facet_override}
        if isinstance(facet_override, dict) and facet_override.get("visible") is False:
            continue
        facet_color = str(
            facet_override.get("color", color)
            if isinstance(facet_override, dict)
            else color
        )
        facet_opacity = opacity
        if isinstance(facet_override, dict) and facet_override.get("opacity") is not None:
            facet_opacity = facet_override["opacity"]
        try:
            facet_opacity = max(0.0, min(1.0, float(facet_opacity)))
        except (TypeError, ValueError):
            facet_opacity = 0.8
        # Zero opacity should have no depth/picking side effects and is
        # equivalent to a hidden facet for the interactive renderer.
        if facet_opacity <= 1.0e-12:
            continue
        group = groups.setdefault(
            (facet_color, round(facet_opacity, 6),
             facet_override.get("edge_color", facet_color),
             float(facet_override.get("edge_width", 0)),
             float(facet_override.get("edge_opacity", 1))),
            {"vertices": [], "faces": [], "face_ids": [], "face_ordinals": [], "facet_millers": []},
        )
        for tri in facet.get("triangles", []):
            base = len(group["vertices"])
            v1, v2, v3 = np.array(tri) * scale + center_offset
            group["vertices"].extend([v1.tolist(), v2.tolist(), v3.tolist()])
            group["faces"].append([base, base + 1, base + 2])
            group["face_ids"].append(facet_id)
            group["face_ordinals"].append(facet_index)
            group["facet_millers"].append(
                list(miller) if isinstance(miller, (list, tuple)) else miller
            )
            visible_facet_indices.add(facet_index)

    for (group_color, group_opacity, edge_color, edge_width, edge_opacity), group in groups.items():
        if not group["vertices"]:
            continue
        entity_meta = {
            "internal": True,
            "source": "bfdh_morphology",
            "facet_count": len(facets),
        }
        if include_face_ids:
            entity_meta.update(
                {
                    "facet_ids": group["face_ids"],
                    "face_ordinals": group["face_ordinals"],
                    "facet_millers": group["facet_millers"],
                }
            )
        entity = mesh_entity(
            group["vertices"],
            group["faces"],
            name="Morphology",
            entity_id="bfdh-morphology",
            color=group_color,
            opacity=group_opacity,
            flatshading=True,
            show_edges=edge_width > 0, edge_color=edge_color,
            edge_width=edge_width, edge_opacity=edge_opacity,
            lighting=dict(ambient=0.75, diffuse=0.9, specular=0.2, roughness=0.25),
            # This is an internal depth-tested entity, not a caller-supplied
            # scene geometry entity.  Keeping the provenance explicit prevents
            # flat/publication validation from treating BFDH as an API input.
            meta=entity_meta,
        )
        if include_face_ids:
            entity["face_ids"] = group["face_ids"]
        traces.extend(
            geometry_entity_traces(
                {"geometry_entities": [entity]},
                include_face_ids=include_face_ids,
            )
        )

    # Add labels
    label_x = []
    label_y = []
    label_z = []
    label_text = []

    for facet_index, facet in enumerate(facets):
        if facet_index not in visible_facet_indices:
            continue
        centroid = np.array(facet["centroid"]) * scale + center_offset
        label_x.append(centroid[0])
        label_y.append(centroid[1])
        label_z.append(centroid[2])
        h, k, ell = facet["miller"]
        label_text.append(f"({h},{k},{ell})")
        
    if label_x:
        label_kwargs = dict(
            x=label_x,
            y=label_y,
            z=label_z,
            mode="text",
            text=label_text,
            textfont=dict(color=color, size=13),
            name="Morphology Labels",
        )
        if include_face_ids:
            label_kwargs["customdata"] = [
                [
                    "facet",
                    facet_instance_id(
                        facet.get("miller", [0, 0, 0]),
                        facet_index,
                        morphology_id="bfdh-morphology",
                    ),
                    list(facet.get("miller", [0, 0, 0])),
                ]
                for facet_index, facet in enumerate(facets)
                if facet_index in visible_facet_indices
            ]
            label_kwargs["hovertemplate"] = "Facet %{customdata[2]}<extra></extra>"
        else:
            label_kwargs["hoverinfo"] = "skip"
        labels = go.Scatter3d(**label_kwargs)
        traces.append(labels)

    return traces
