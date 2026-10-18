from __future__ import annotations

import numpy as np

from .common import go

from .geometry import geometry_entity_traces, mesh_entity
from .picking import facet_instance_id


def _morphology_traces(scene: dict, style: dict) -> list[go.Trace]:
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
    from .style.resolver import normalize_style_overrides
    overrides = normalize_style_overrides(style.get("style_overrides"))
    parent_override = overrides.get("bfdh-morphology") or overrides.get("morphology:bfdh-morphology")
    if isinstance(parent_override, dict):
        if parent_override.get("visible") is False:
            return []
        color = str(parent_override.get("color", color))
        opacity = parent_override.get("opacity", opacity)

    # Center the morphology at the center of the unit cell
    center_offset = np.zeros(3)
    if scene.get("M") is not None:
        a = np.array(scene["M"][0], dtype=float)
        b = np.array(scene["M"][1], dtype=float)
        c = np.array(scene["M"][2], dtype=float)
        center_offset = (a + b + c) / 2.0

    traces = []

    # Build the Wulff surface through the same generic mesh-entity path used by
    # caller-supplied solids.  Facet overrides are materialised as separate
    # meshes rather than rgba entries in one ``facecolor`` array.  Plotly's
    # Mesh3d alpha handling has varied across releases; a transparent
    # ``rgba(0,0,0,0)`` face could therefore fall back to opaque black.  A
    # hidden facet is omitted entirely and an opacity override becomes the
    # trace opacity, which is deterministic in both WebGL and static export.
    groups: dict[tuple[str, float], dict[str, list]] = {}
    visible_facet_indices: set[int] = set()
    for facet_index, facet in enumerate(facets):
        miller = facet.get("miller", [0, 0, 0])
        facet_id = facet_instance_id(miller, facet_index, morphology_id="bfdh-morphology")
        facet_override = overrides.get(facet_id) if isinstance(overrides, dict) else None
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
            (facet_color, round(facet_opacity, 6)),
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

    for (group_color, group_opacity), group in groups.items():
        if not group["vertices"]:
            continue
        entity = mesh_entity(
            group["vertices"],
            group["faces"],
            name="Morphology",
            entity_id="bfdh-morphology",
            color=group_color,
            opacity=group_opacity,
            flatshading=True,
            lighting=dict(ambient=0.75, diffuse=0.9, specular=0.2, roughness=0.25),
            # This is an internal depth-tested entity, not a caller-supplied
            # scene geometry entity.  Keeping the provenance explicit prevents
            # flat/publication validation from treating BFDH as an API input.
            meta={
                "internal": True,
                "source": "bfdh_morphology",
                "facet_ids": group["face_ids"],
                "face_ordinals": group["face_ordinals"],
                "facet_millers": group["facet_millers"],
                "facet_count": len(facets),
            },
        )
        entity["face_ids"] = group["face_ids"]
        traces.extend(geometry_entity_traces({"geometry_entities": [entity]}))

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
        labels = go.Scatter3d(
            x=label_x,
            y=label_y,
            z=label_z,
            mode="text",
            text=label_text,
            textfont=dict(color=color, size=13),
            customdata=[
                [
                    "facet",
                    facet_instance_id(facet.get("miller", [0, 0, 0]), facet_index, morphology_id="bfdh-morphology"),
                    list(facet.get("miller", [0, 0, 0])),
                ]
                for facet_index, facet in enumerate(facets)
                if facet_index in visible_facet_indices
            ],
            hovertemplate="Facet %{customdata[2]}<extra></extra>",
            name="Morphology Labels"
        )
        traces.append(labels)

    return traces
