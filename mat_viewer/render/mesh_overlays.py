"""Compile polyhedron and isosurface overlays into render primitives."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from .contracts import Primitive, TriangleMeshPrimitive
from .geometry import (
    mesh_primitive,
    polyhedron_edges_primitive,
    polyhedron_primitive,
)


def _value(record: Any, *names: str, default: Any = ...):
    for name in names:
        if isinstance(record, Mapping) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    if default is not ...:
        return default
    raise ValueError(f"record is missing required field; tried {', '.join(names)}")


def polyhedron_primitives(
    scene: Mapping[str, Any], topology_data: Mapping[str, Any] | None
) -> list[Primitive]:
    results: list[Primitive] = []
    explicit = list(scene.get("polyhedra") or [])
    if topology_data:
        for specification in topology_data.get("spec_results") or []:
            for overlay in specification.get("overlays") or []:
                explicit.append(
                    {
                        "vertices": overlay.get("shell_coords"),
                        "faces": (overlay.get("hull") or {}).get("simplices"),
                        "color": overlay.get("color")
                        or specification.get("color")
                        or "#7C5CBF",
                        "opacity": specification.get("opacity", 0.55),
                        "edge_opacity": specification.get("edge_opacity", 0.9),
                        "spec_id": specification.get("spec_id"),
                    }
                )
    for index, item in enumerate(explicit):
        vertices = _value(item, "vertices", "shell_coords", default=[])
        faces = _value(item, "faces", "simplices", default=[])
        if vertices is None or faces is None or len(vertices) == 0 or len(faces) == 0:
            continue
        semantic_id = f"polyhedron:{index}:{_value(item, 'spec_id', default='')}"
        color = _value(item, "color", default="#7C5CBF")
        results.append(
            polyhedron_primitive(
                semantic_id,
                vertices,
                faces,
                color,
                alpha=float(_value(item, "opacity", default=0.55)),
                metadata={
                    "kind": "polyhedron",
                    "spec_id": _value(item, "spec_id", default=None),
                },
            )
        )
        results.append(
            polyhedron_edges_primitive(
                f"{semantic_id}:edges",
                vertices,
                faces,
                color,
                alpha=float(_value(item, "edge_opacity", default=0.9)),
            )
        )
    return results


def isosurface_primitives(
    scene: Mapping[str, Any],
) -> tuple[list[TriangleMeshPrimitive], list[str]]:
    """Consume meshes prepared lazily by the optional cube adapter."""
    entries = scene.get("isosurfaces")
    cube_data = scene.get("cube_data")
    if entries is None and cube_data is not None:
        for name in ("isosurfaces", "surface_meshes"):
            candidate = (
                cube_data.get(name)
                if isinstance(cube_data, Mapping)
                else getattr(cube_data, name, None)
            )
            if candidate is not None:
                entries = candidate
                break
    if entries is None:
        if cube_data is not None:
            raise RuntimeError(
                "cube_data has no prepared isosurface meshes; the [cube] adapter "
                "must lazily run marching cubes and populate scene['isosurfaces']"
            )
        return [], []

    results: list[TriangleMeshPrimitive] = []
    warnings: list[str] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, Mapping):
            vertices = entry.get("vertices")
            if vertices is None:
                vertices = entry.get("verts")
            triangles = entry.get("triangles")
            if triangles is None:
                triangles = entry.get("faces")
            normals = entry.get("normals")
            name = str(entry.get("id") or entry.get("name") or index)
            color = entry.get("color", "#D55E00" if index == 0 else "#0072B2")
            opacity = float(entry.get("opacity", 0.55))
            metadata = {
                "kind": "isosurface",
                "name": name,
                "phase": entry.get("phase"),
                "level": entry.get("level"),
            }
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            vertices, triangles = entry[0], entry[1]
            normals = entry[2] if len(entry) >= 3 else None
            name = str(index)
            color = "#D55E00" if index == 0 else "#0072B2"
            opacity = 0.55
            metadata = {"kind": "isosurface", "name": name}
        else:
            warnings.append(
                f"isosurface {index} has an unsupported mesh record and was skipped"
            )
            continue
        if vertices is None or triangles is None:
            warnings.append(f"isosurface {name} has no vertices/faces and was skipped")
            continue
        vertex_array = np.asarray(vertices, dtype=float)
        triangle_array = np.asarray(triangles, dtype=np.int64)
        if len(vertex_array) == 0 or len(triangle_array) == 0:
            warnings.append(f"isosurface {name} is empty and was skipped")
            continue
        results.append(
            mesh_primitive(
                f"isosurface:{index}:{name}",
                vertex_array,
                triangle_array,
                color,
                normals=normals,
                alpha=opacity,
                metadata=metadata,
            )
        )
    return results, warnings
