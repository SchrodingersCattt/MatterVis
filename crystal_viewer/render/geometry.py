"""Render arbitrary Cartesian mesh entities as real 3-D scene geometry.

Unlike a paper-coordinate annotation, a geometry entity is sent to Plotly as
``Mesh3d`` vertices and faces.  Opaque entities therefore share the same WebGL
depth buffer as atoms, bonds, BFDH facets, and coordination polyhedra.  The
module deliberately contains no chemistry-specific assumptions: callers can
attach any validated triangular mesh to ``scene["geometry_entities"]``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import plotly.graph_objects as go

from ..math.geometry import cylinder_vertices_faces, validate_mesh


_DEFAULT_COLOR = "#7C5CBF"


def _json_safe(value: Any) -> Any:
    """Convert common NumPy/container values to JSON primitives.

    Scene entities are persisted by :func:`scene_json`; keeping this small
    normaliser here means callers can pass NumPy metadata (for example an
    ``axis_cartesian`` array) without turning an otherwise valid entity into
    a non-serialisable object.  Unknown scalar objects are left untouched so
    Plotly can provide its usual contextual validation error if one is used.
    """

    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _opacity(value: Any, *, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number in [0, 1]") from exc
    if not np.isfinite(out) or out < 0.0 or out > 1.0:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return out


def _normalise_edges(
    edges: Iterable[Iterable[int]],
    vertex_count: int,
    *,
    name: str = "edges",
) -> np.ndarray:
    """Validate an explicit edge list and return an ``N×2`` integer array.

    Mesh faces are validated separately because they may be polygons.  Edge
    lists, however, are often hand-written for an open channel and must not
    be allowed to leak an ``IndexError`` from the trace builder.  Keeping the
    checks here gives both the convenience builders and raw scene payloads the
    same failure semantics.
    """

    if isinstance(edges, (str, bytes)):
        raise ValueError(f"{name} must be an iterable of index pairs")
    try:
        rows = list(edges)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of index pairs") from exc

    normalised: list[list[int]] = []
    for edge_index, raw_edge in enumerate(rows):
        try:
            values = list(raw_edge)
        except TypeError as exc:
            raise ValueError(f"{name}[{edge_index}] is not an index pair") from exc
        if len(values) != 2:
            raise ValueError(f"{name}[{edge_index}] must contain exactly two indices")
        pair: list[int] = []
        for value in values:
            if isinstance(value, (bool, np.bool_)):
                raise ValueError(f"{name}[{edge_index}] contains a non-integer index")
            try:
                integer = int(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"{name}[{edge_index}] contains a non-integer index"
                ) from exc
            if isinstance(value, (float, np.floating)) and float(value) != integer:
                raise ValueError(f"{name}[{edge_index}] contains a non-integer index")
            if integer < 0 or integer >= vertex_count:
                raise ValueError(
                    f"{name}[{edge_index}] contains an index outside vertices"
                )
            pair.append(integer)
        if pair[0] == pair[1]:
            raise ValueError(f"{name}[{edge_index}] is degenerate")
        normalised.append(pair)

    return np.asarray(normalised, dtype=int).reshape(-1, 2)


def mesh_entity(
    vertices: Iterable[Iterable[float]],
    faces: Iterable[Iterable[int]],
    *,
    name: str = "geometry",
    entity_id: str | None = None,
    color: str = _DEFAULT_COLOR,
    opacity: float = 1.0,
    visible: bool = True,
    flatshading: bool = True,
    lighting: Mapping[str, Any] | None = None,
    show_edges: bool = False,
    edge_color: str | None = None,
    edge_width: float = 2.0,
    edge_opacity: float = 1.0,
    edges: Iterable[Iterable[int]] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe arbitrary mesh entity for a MatterVis scene.

    Parameters
    ----------
    vertices, faces:
        Cartesian vertices (``N×3``) and polygon/triangle index lists.  Polygon
        faces are triangulated with a fan while preserving their winding.
    name, entity_id:
        Human-readable and stable identifiers.  ``entity_id`` is used in trace
        metadata for picking/inspection and may be omitted.
    opacity:
        Surface opacity.  Keep this at ``1.0`` when exact per-pixel occlusion
        against other meshes is required; Plotly's transparent WebGL surfaces
        use approximate trace-level compositing.
    show_edges:
        Add a true 3-D edge trace alongside the surface.  This is useful for
        an open channel or a low-opacity surface and is still depth-tested in
        the 3-D scene (it is not a 2-D overlay).

    Returns
    -------
    dict
        A scene-ready entity.  Arrays are plain lists so the value can be
        serialised through ``scene_json`` without a custom encoder.
    """

    vertex_array, face_array = validate_mesh(vertices, faces)
    opacity_value = _opacity(opacity, name="opacity")
    edge_opacity_value = _opacity(edge_opacity, name="edge_opacity")
    try:
        edge_width_value = float(edge_width)
    except (TypeError, ValueError) as exc:
        raise ValueError("edge_width must be a finite non-negative number") from exc
    if not np.isfinite(edge_width_value) or edge_width_value < 0.0:
        raise ValueError("edge_width must be a finite non-negative number")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")

    edge_array: np.ndarray | None = None
    if edges is not None:
        edge_array = _normalise_edges(edges, len(vertex_array))

    color_value = _DEFAULT_COLOR if color is None else str(color)
    edge_color_value = color_value if edge_color is None else str(edge_color)
    entity: dict[str, Any] = {
        "kind": "mesh",
        "name": name,
        "vertices": vertex_array.tolist(),
        "faces": face_array.tolist(),
        "color": color_value,
        "opacity": opacity_value,
        "visible": bool(visible),
        "flatshading": bool(flatshading),
        "show_edges": bool(show_edges),
        "edge_color": edge_color_value,
        "edge_width": edge_width_value,
        "edge_opacity": edge_opacity_value,
    }
    if edge_array is not None:
        entity["edges"] = edge_array.tolist()
    if entity_id is not None:
        entity["id"] = str(entity_id)
    if lighting is not None:
        entity["lighting"] = _json_safe(lighting)
    if meta is not None:
        entity["meta"] = _json_safe(meta)
    return entity


def cylinder_entity(
    center: Iterable[float],
    axis: Iterable[float],
    radius: float,
    length: float,
    *,
    segments: int = 32,
    caps: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a scene-ready cylinder mesh.

    The default is an *open* cylinder (no end caps), which is the least
    misleading representation of a through-channel: looking along the axis
    leaves both openings visible instead of placing an opaque disk across the
    hole.  Pass ``caps=True`` for a solid cylinder entity.
    """

    vertices, faces = cylinder_vertices_faces(
        center,
        axis,
        radius,
        length,
        segments=segments,
        caps=caps,
    )
    custom_edges = kwargs.pop("edges", None)
    side_edges = []
    segment_count = (len(vertices) - (2 if caps else 0)) // 2
    for index in range(segment_count):
        nxt = (index + 1) % segment_count
        side_edges.extend(
            [
                [index, nxt],
                [segment_count + index, segment_count + nxt],
            ]
        )
    # A seam at every polygon segment makes a high-resolution cylinder read
    # like a striped sheet in a side view.  Keep a few deterministic seams for
    # orientation/depth cues while leaving the surface itself responsible for
    # the silhouette and occlusion.
    seam_count = min(4, segment_count)
    seam_indices = np.linspace(0, segment_count - 1, seam_count, dtype=int)
    side_edges.extend([[index, segment_count + index] for index in seam_indices])
    return mesh_entity(
        vertices,
        faces,
        edges=side_edges if custom_edges is None else custom_edges,
        **kwargs,
    )


def through_cylinder_entity(
    lattice: Iterable[Iterable[float]],
    direction_hkl: Iterable[int],
    radius: float,
    *,
    center_frac: Iterable[float] = (0.5, 0.5, 0.5),
    periods: float = 1.0,
    segments: int = 32,
    caps: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a cylinder aligned with a crystallographic lattice direction.

    ``lattice`` uses MatterVis' row-vector convention: fractional
    coordinates map to Cartesian coordinates as ``frac @ lattice``.  The
    reduced Miller/index direction is converted to the Cartesian vector
    ``h*a + k*b + l*c`` before constructing the mesh.  This convenience layer
    keeps the direction and centre used for rendering identical to a
    through-cylinder operation supplied by MolCrysKit and avoids accidental
    ``[110]``/``[100]`` mismatches in caller scripts.

    ``periods`` controls the physical length in repeats of the direction
    vector; the default one-period open cylinder is suitable for a periodic
    channel.  Use ``caps=True`` when the entity represents a solid rather
    than a void wall.
    """

    try:
        lattice_array = np.asarray(lattice, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("lattice must be a finite 3×3 numeric array") from exc
    if (
        lattice_array.shape != (3, 3)
        or not np.all(np.isfinite(lattice_array))
        or abs(float(np.linalg.det(lattice_array))) <= 1e-12
    ):
        raise ValueError("lattice must be a non-singular finite 3×3 array")

    try:
        direction_array = np.asarray(list(direction_hkl), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("direction_hkl must contain three integers") from exc
    if (
        direction_array.shape != (3,)
        or not np.all(np.isfinite(direction_array))
        or not np.allclose(direction_array, np.rint(direction_array))
    ):
        raise ValueError("direction_hkl must contain three integers")
    direction = np.rint(direction_array).astype(int)
    if not np.any(direction):
        raise ValueError("direction_hkl must be non-zero")
    divisor = math.gcd(*(abs(int(value)) for value in direction))
    direction //= divisor
    first_nonzero = int(direction[np.flatnonzero(direction)[0]])
    if first_nonzero < 0:
        direction *= -1

    try:
        center_frac_array = np.asarray(list(center_frac), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("center_frac must contain three finite numbers") from exc
    if center_frac_array.shape != (3,) or not np.all(np.isfinite(center_frac_array)):
        raise ValueError("center_frac must contain three finite numbers")
    try:
        periods_value = float(periods)
    except (TypeError, ValueError) as exc:
        raise ValueError("periods must be a finite positive number") from exc
    if not np.isfinite(periods_value) or periods_value <= 0.0:
        raise ValueError("periods must be a finite positive number")

    axis = direction.astype(float) @ lattice_array
    length = float(np.linalg.norm(axis)) * periods_value
    center = center_frac_array @ lattice_array
    entity = cylinder_entity(
        center=center,
        axis=axis,
        radius=radius,
        length=length,
        segments=segments,
        caps=caps,
        **kwargs,
    )
    entity_meta = dict(entity.get("meta") or {})
    entity_meta.update(
        {
            "direction_hkl": direction.tolist(),
            "center_frac": center_frac_array.tolist(),
            "axis_cartesian": axis.tolist(),
            "periods": periods_value,
            "length_A": length,
        }
    )
    entity["meta"] = entity_meta
    return entity


def validate_geometry_style(scene: Mapping[str, Any], style: Mapping[str, Any]) -> None:
    """Reject render modes that cannot provide 3-D geometry occlusion.

    The ``flat`` and ``flat + ortep`` paths deliberately use 2-D/billboard
    primitives.  Accepting a mesh entity in those paths would silently drop
    its depth relationship (or make it look like a paper overlay), which is
    exactly the failure mode this API is intended to prevent.  Callers that
    attach geometry therefore have to select the real ``Mesh3d`` material.
    """

    entities = scene.get("geometry_entities")
    if entities is None:
        return
    if not isinstance(entities, (list, tuple)):
        raise ValueError('scene["geometry_entities"] must be a list of mesh entities')
    if not entities:
        return
    if str(style.get("material", "mesh")) != "mesh":
        raise ValueError(
            "scene geometry_entities require material='mesh'; "
            "the flat/billboard renderer cannot provide 3-D occlusion"
        )


def _entity_edges(
    faces: np.ndarray,
    explicit_edges: Any = None,
    *,
    vertex_count: int,
) -> list[tuple[int, int]]:
    """Return unique undirected mesh edges in deterministic order."""

    if explicit_edges is not None:
        validated = _normalise_edges(explicit_edges, vertex_count)
        return sorted(
            {tuple(sorted((int(edge[0]), int(edge[1])))) for edge in validated}
        )

    edges: set[tuple[int, int]] = set()
    for a, b, c in np.asarray(faces, dtype=int):
        edges.update(
            {
                tuple(sorted((int(a), int(b)))),
                tuple(sorted((int(b), int(c)))),
                tuple(sorted((int(c), int(a)))),
            }
        )
    return sorted(edges)


def _trace_meta(entity: Mapping[str, Any], role: str) -> dict[str, Any]:
    raw = entity.get("meta")
    meta = dict(raw) if isinstance(raw, Mapping) else {}
    meta["mv_role"] = role
    meta.setdefault("kind", "geometry_entity")
    if entity.get("id") is not None:
        meta["geometry_id"] = str(entity["id"])
    return meta


def geometry_entity_traces(scene: Mapping[str, Any]) -> list[go.BaseTraceType]:
    """Build Plotly traces for ``scene["geometry_entities"]``.

    Every surface is a genuine ``Mesh3d`` trace in world coordinates.  No
    projection to paper coordinates occurs here, so Plotly can resolve depth
    against atoms and other meshes.  Invalid hand-written scene entries raise
    a contextual ``ValueError``; callers using :func:`mesh_entity` get the
    same validation before the scene is assembled.
    """

    entities = scene.get("geometry_entities")
    if entities is None:
        return []
    if not isinstance(entities, (list, tuple)):
        raise ValueError('scene["geometry_entities"] must be a list of mesh entities')

    traces: list[go.BaseTraceType] = []
    for entity_index, raw_entity in enumerate(entities):
        if not isinstance(raw_entity, Mapping):
            raise ValueError(f"geometry_entities[{entity_index}] must be a mapping")
        if not bool(raw_entity.get("visible", True)):
            continue
        try:
            vertices, faces = validate_mesh(
                raw_entity.get("vertices"), raw_entity.get("faces")
            )
            opacity_value = _opacity(raw_entity.get("opacity", 1.0), name="opacity")
            raw_color = raw_entity.get("color", _DEFAULT_COLOR)
            color = _DEFAULT_COLOR if raw_color is None else str(raw_color)
            name = str(raw_entity.get("name", raw_entity.get("id", "geometry")))
            mesh_kwargs: dict[str, Any] = {
                "x": vertices[:, 0],
                "y": vertices[:, 1],
                "z": vertices[:, 2],
                "i": faces[:, 0],
                "j": faces[:, 1],
                "k": faces[:, 2],
                "color": color,
                "opacity": opacity_value,
                "flatshading": bool(raw_entity.get("flatshading", True)),
                "hoverinfo": "skip",
                "showlegend": False,
                "name": name,
                "meta": _json_safe(_trace_meta(raw_entity, "geometry_entity")),
            }
            lighting = raw_entity.get("lighting")
            if isinstance(lighting, Mapping):
                mesh_kwargs["lighting"] = dict(lighting)
            traces.append(go.Mesh3d(**mesh_kwargs))

            if bool(raw_entity.get("show_edges", False)):
                edge_list = _entity_edges(
                    faces,
                    raw_entity.get("edges"),
                    vertex_count=len(vertices),
                )
                if edge_list:
                    xs: list[float | None] = []
                    ys: list[float | None] = []
                    zs: list[float | None] = []
                    for start, end in edge_list:
                        xs.extend(
                            [float(vertices[start, 0]), float(vertices[end, 0]), None]
                        )
                        ys.extend(
                            [float(vertices[start, 1]), float(vertices[end, 1]), None]
                        )
                        zs.extend(
                            [float(vertices[start, 2]), float(vertices[end, 2]), None]
                        )
                    edge_opacity_value = _opacity(
                        raw_entity.get("edge_opacity", 1.0), name="edge_opacity"
                    )
                    try:
                        edge_width = float(raw_entity.get("edge_width", 2.0))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            "edge_width must be a finite non-negative number"
                        ) from exc
                    if not np.isfinite(edge_width) or edge_width < 0:
                        raise ValueError(
                            "edge_width must be a finite non-negative number"
                        )
                    traces.append(
                        go.Scatter3d(
                            x=xs,
                            y=ys,
                            z=zs,
                            mode="lines",
                            line={
                                "color": str(raw_entity.get("edge_color") or color),
                                "width": edge_width,
                            },
                            opacity=edge_opacity_value,
                            hoverinfo="skip",
                            showlegend=False,
                            name=f"{name} edges",
                            meta=_json_safe(_trace_meta(raw_entity, "geometry_entity_edge")),
                        )
                    )
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(
                f"geometry_entities[{entity_index}] is invalid: {exc}"
            ) from exc
    return traces


__all__ = [
    "cylinder_entity",
    "geometry_entity_traces",
    "mesh_entity",
    "through_cylinder_entity",
    "validate_geometry_style",
]
