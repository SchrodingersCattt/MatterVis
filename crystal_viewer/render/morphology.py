from __future__ import annotations

import plotly.graph_objects as go
import numpy as np

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

    # Center the morphology at the center of the unit cell
    center_offset = np.zeros(3)
    if scene.get("M") is not None:
        a = np.array(scene["M"][0], dtype=float)
        b = np.array(scene["M"][1], dtype=float)
        c = np.array(scene["M"][2], dtype=float)
        center_offset = (a + b + c) / 2.0

    traces = []
    
    # We need to build a single Mesh3d for the morphology
    all_vertices = []
    i_indices = []
    j_indices = []
    k_indices = []
    
    vertex_offset = 0
    
    for facet in facets:
        triangles = facet.get("triangles", [])
        for tri in triangles:
            v1, v2, v3 = np.array(tri) * scale + center_offset
            
            i_indices.append(vertex_offset)
            j_indices.append(vertex_offset + 1)
            k_indices.append(vertex_offset + 2)
            
            all_vertices.extend([v1.tolist(), v2.tolist(), v3.tolist()])
            vertex_offset += 3

    if all_vertices:
        all_vertices = np.array(all_vertices)
        mesh = go.Mesh3d(
            x=all_vertices[:, 0],
            y=all_vertices[:, 1],
            z=all_vertices[:, 2],
            i=i_indices,
            j=j_indices,
            k=k_indices,
            color=color,
            opacity=opacity,
            flatshading=True,
            lighting=dict(ambient=0.75, diffuse=0.9, specular=0.2, roughness=0.25),
            hoverinfo="skip",
            name="Morphology"
        )
        traces.append(mesh)

    # Add labels
    label_x = []
    label_y = []
    label_z = []
    label_text = []
    
    for facet in facets:
        centroid = np.array(facet["centroid"]) * scale + center_offset
        label_x.append(centroid[0])
        label_y.append(centroid[1])
        label_z.append(centroid[2])
        h, k, l = facet["miller"]
        label_text.append(f"({h},{k},{l})")
        
    if label_x:
        labels = go.Scatter3d(
            x=label_x,
            y=label_y,
            z=label_z,
            mode="text",
            text=label_text,
            textfont=dict(color=color, size=13),
            hoverinfo="skip",
            name="Morphology Labels"
        )
        traces.append(labels)

    return traces
