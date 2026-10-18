"""Stable semantic identities shared by renderers and browser picking.

Plotly trace indexes and atom labels are presentation details.  They are not
safe selection keys: a label can occur in a periodic image, a batched mesh can
contain many independent objects, and redraws are free to reorder traces.  The
helpers in this module build a small JSON-safe target contract that is used by
interaction traces, server state, and the browser controller.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, Iterable


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _token(value: Any) -> str:
    """Return a deterministic, URL/JSON friendly token for identity parts."""

    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(_token(item) for item in value)
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return _text(value)


def atom_instance_id(atom: Mapping[str, Any], display_index: int | None = None) -> str:
    """Build an exact displayed-atom identity.

    Loader-provided ``_mv_instance_id``/``instance_id`` values take precedence.
    Otherwise source index, periodic image, disorder component, and a stable
    label/element/coordinate fingerprint are retained.  The optional display
    index is accepted for API compatibility but is never part of the identity:
    draw-list order is presentation state and changes during redraws.
    """

    explicit = atom.get("_mv_instance_id") or atom.get("instance_id")
    if explicit:
        return _text(explicit)
    # Source indices are loader-owned identity.  Never append the transient
    # display ordinal when one is present: redraws may reorder copies while
    # preserving the same source atom, and persisted style/selection targets
    # must continue to resolve to that atom.  The display ordinal is a
    # last-resort identity only for hand-built payloads with no source token.
    source_marker = object()
    source = atom.get("_source_index", source_marker)
    if source is source_marker:
        source = atom.get("source_index", source_marker)
    has_source = source is not source_marker and source not in (None, "")
    if not has_source:
        # Hand-built scenes may omit loader source indices.  Derive a stable
        # token from the atom's semantic fields instead of embedding the
        # current draw-list ordinal (which changes when replicas are filtered
        # or reordered between frames).  Identical anonymous atoms are
        # inherently indistinguishable without an upstream identity token.
        coordinates = atom.get("cart")
        if coordinates is None:
            coordinates = atom.get("coords")
        if coordinates is None:
            coordinates = atom.get("coordinates")
        source = "|".join(
            (
                f"label:{_token(atom.get('label') or atom.get('name'))}",
                f"elem:{_token(atom.get('elem') or atom.get('element'))}",
                f"cart:{_token(coordinates)}",
            )
        )
    image = (
        atom.get("_display_copy_id")
        or atom.get("_replica_id")
        or atom.get("_pbc_image")
        or atom.get("_image")
        or atom.get("_boundary_image")
        or atom.get("_image_shift")
    )
    symmetry = (
        atom.get("_symop_index")
        if atom.get("_symop_index") not in (None, "")
        else atom.get("_symmetry_operation_index")
    )
    disorder = (
        atom.get("_disorder_part")
        or atom.get("disorder_part")
        or atom.get("_disorder_group")
        or atom.get("disorder_group")
        or atom.get("dg")
    )
    parts = [f"source:{_token(source)}"]
    if symmetry not in (None, ""):
        parts.append(f"symmetry:{_token(symmetry)}")
    # ``atoms_under_symmetry`` historically encoded the operation ordinal in
    # ``_image_shift=(op,0,0)``. Once explicit symmetry provenance is present,
    # suppress that legacy token so IDs describe source/symmetry scope rather
    # than pretending the operation is a periodic replica.
    legacy_symmetry_image = False
    if symmetry not in (None, ""):
        try:
            legacy_symmetry_image = tuple(int(value) for value in image) == (
                int(symmetry),
                0,
                0,
            )
        except (TypeError, ValueError):
            legacy_symmetry_image = False
    if image not in (None, "", (), [], [0, 0, 0]) and not legacy_symmetry_image:
        parts.append(f"image:{_token(image)}")
    if disorder not in (None, "", "."):
        parts.append(f"disorder:{_token(disorder)}")
    return "atom:" + "|".join(parts)


def bond_instance_id(
    bond: Mapping[str, Any],
    atoms: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None = None,
    bond_index: int | None = None,
) -> str:
    """Build a stable identity for a displayed bond segment."""

    explicit = bond.get("_mv_instance_id") or bond.get("instance_id") or bond.get("bond_id")
    if explicit:
        return _text(explicit)
    atoms = atoms or []
    i, j = bond.get("i"), bond.get("j")
    try:
        ai = atom_instance_id(atoms[int(i)], int(i)) if 0 <= int(i) < len(atoms) else _text(i)
        aj = atom_instance_id(atoms[int(j)], int(j)) if 0 <= int(j) < len(atoms) else _text(j)
    except (TypeError, ValueError):
        ai, aj = _text(i), _text(j)
    endpoint = "--".join(sorted((ai, aj)))
    image = bond.get("image") or bond.get("image_shift") or bond.get("pbc_image")
    suffix = f"|image:{_token(image)}" if image not in (None, "", (), [], [0, 0, 0]) else ""
    if not endpoint:
        endpoint = f"index:{bond_index if bond_index is not None else 0}"
    return f"bond:{endpoint}{suffix}"


def polyhedron_instance_id(spec_id: Any, overlay: Mapping[str, Any], ordinal: int | None = None) -> str:
    """Identify one concrete topology/polyhedron overlay, including PBC image.

    ``ordinal`` remains accepted for callers that enumerate overlays, but it
    is intentionally ignored.  Paint grouping and skipped/hidden overlays can
    reorder that ordinal, so source/label/coordinate identity is required for
    a target that survives redraws.
    """

    explicit = overlay.get("instance_id") or overlay.get("polyhedron_id")
    if explicit:
        return _text(explicit)
    source_marker = object()
    source = overlay.get("center_source_index", source_marker)
    if source is source_marker or source in (None, ""):
        source = overlay.get("center_label", source_marker)
    if source is source_marker or source in (None, ""):
        coordinates = overlay.get("center_coords")
        if coordinates is None:
            coordinates = overlay.get("center_cart")
        source = _token(coordinates if coordinates is not None else "anonymous")
    image = overlay.get("center_image_shift", overlay.get("center_image"))
    source_token = _token(source)
    parts = [f"spec:{_token(spec_id)}", f"source:{source_token}"]
    if image not in (None, "", (), [], [0, 0, 0]):
        parts.append(f"image:{_token(image)}")
    return "polyhedron:" + "|".join(parts)


def ensure_polyhedron_instance_ids(topology_data: Mapping[str, Any] | None) -> None:
    """Stamp fallback IDs onto topology overlays in renderer order.

    Geometry produced by the app already carries source/image identity, but
    callers can provide hand-built topology dictionaries (and old cached
    payloads may lack those fields).  Mesh traces are grouped by paint style
    and skip non-drawable overlays, while the invisible selection trace walks
    every visible centre.  Assigning one ID in the shared spec/overlay order
    before either path runs keeps ``meta.face_ids`` and selection customdata in
    lockstep even in that legacy case.

    The payload is intentionally updated in place: the same topology wrapper
    is used by the background and selection painters and retaining the stamp
    avoids depending on whichever painter happens to run first.
    """

    if not isinstance(topology_data, Mapping):
        return
    spec_results = topology_data.get("spec_results") or []
    ordinal = 0
    for entry in spec_results:
        if not isinstance(entry, Mapping):
            continue
        spec_id = str(entry.get("spec_id") or "")
        overlays = entry.get("overlays") or []
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            if not overlay.get("instance_id"):
                overlay["instance_id"] = polyhedron_instance_id(spec_id, overlay, ordinal)
            if overlay.get("center_coords") is not None and overlay.get("visible", True):
                ordinal += 1


def facet_instance_id(miller: Any, facet_index: int, *, morphology_id: str = "bfdh-morphology") -> str:
    """Identify one BFDH facet independently of its triangle ordinal."""

    return f"facet:{_text(morphology_id)}:{_token(miller)}:{int(facet_index)}"


def target(
    kind: str,
    entity_id: Any,
    *,
    scene_id: Any = None,
    parent_id: Any = None,
    subentity_id: Any = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a normalized, JSON-safe semantic target payload."""

    value: dict[str, Any] = {
        "kind": _text(kind).lower(),
        "id": _text(entity_id),
    }
    if scene_id not in (None, ""):
        value["scene_id"] = _text(scene_id)
    if parent_id not in (None, ""):
        value["parent_id"] = _text(parent_id)
    if subentity_id not in (None, ""):
        value["subentity_id"] = _text(subentity_id)
    if metadata:
        value["metadata"] = dict(metadata)
    return value


def normalize_target(raw: Any) -> dict[str, Any] | None:
    """Validate a target received from browser/API state.

    Unknown metadata is retained for forward compatibility, but malformed
    target kinds/IDs are rejected so stale browser events cannot select an
    unrelated object after a scene redraw.
    """

    if not isinstance(raw, Mapping):
        return None
    kind = _text(raw.get("kind")).lower()
    entity_id = _text(raw.get("id") or raw.get("target_id") or raw.get("instance_id"))
    if not kind or not entity_id:
        return None
    allowed = {"atom", "bond", "molecule", "part", "polyhedron", "facet", "morphology", "geometry", "geometry_entity"}
    if kind not in allowed:
        return None
    value = target(
        kind,
        entity_id,
        scene_id=raw.get("scene_id"),
        parent_id=raw.get("parent_id") or raw.get("parent_target_id"),
        subentity_id=raw.get("subentity_id") or raw.get("face_id"),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else None,
    )
    # Keep useful identity fields at the top level for old right-click code and
    # human-readable inspectors while treating ``id`` as the canonical key.
    for key in ("label", "element", "fragment_label", "spec_id", "miller", "source_index", "image_shift"):
        if key in raw:
            value[key] = raw[key]
    return value


# Picking is deliberately independent of Plotly.  Plotly's hover payload can
# contain several points for one pixel (and transparent marker traces are
# often reported before the depth-tested surface that is visually underneath
# them), so a caller needs a stable policy before it publishes a semantic
# target.  Keep the policy here as small, pure functions that can also be used
# by a non-Plotly adapter or a browser fixture.
PICK_DEPTH_EPSILON = 1.0e-6
PICK_OPACITY_EPSILON = 1.0e-3

# Larger values win only after camera depth and occlusion have been resolved.
# In particular, an atom wins a tie with a bond at an atom centre, while a
# polyhedron/mesh face wins a tie with a centre marker.  A bond that is truly
# in front still wins by depth; this table is a deterministic tie breaker, not
# a painter-order override.
PICK_KIND_PRIORITY: dict[str, int] = {
    "geometry": 500,
    "geometry_entity": 500,
    "facet": 460,
    "morphology": 450,
    "polyhedron": 440,
    "atom": 360,
    "molecule": 320,
    "part": 310,
    "bond": 260,
    "label": 100,
}

# Pixel tolerances are metadata for adapters that need to merge a thin line's
# hover area with a nearby surface.  They are intentionally conservative:
# broad invisible markers must not steal a click from an atom or a face.
PICK_TOLERANCE_PX: dict[str, float] = {
    "geometry": 3.0,
    "geometry_entity": 3.0,
    "facet": 4.0,
    "morphology": 4.0,
    "polyhedron": 8.0,
    "atom": 10.0,
    "bond": 5.0,
    "label": 2.0,
}


def _pick_kind(value: Any) -> str:
    normalized = _text(value).lower().replace("-", "_")
    # Interaction traces use role-specific names while their semantic target
    # has the base kind.  Canonicalising here keeps probe metadata tied to the
    # same priority/tolerance bucket as the underlying atom/bond/face.
    return {
        "atom_selection": "atom",
        "bond_selection": "bond",
        "polyhedron_selection": "polyhedron",
        "geometry_entity_edge": "geometry",
    }.get(normalized, normalized)


def _finite_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _order_value(value: Any) -> int | float:
    """Coerce Plotly trace/point ordinals without int(inf) failures."""

    result = _finite_float(value, math.inf)
    return int(result) if math.isfinite(result) else math.inf


def pick_trace_metadata(
    kind: str,
    *,
    opacity: Any = 1.0,
    pickable: bool = True,
    occludes: bool | None = None,
    probe: bool | None = None,
    tolerance_px: Any = None,
) -> dict[str, Any]:
    """Return stable metadata consumed by semantic hit-test adapters.

    ``occludes`` defaults to true only for an opaque visual trace.  Interaction
    marker traces should pass ``probe=True``: they provide an ID when a mesh
    cannot expose customdata, but must never hide a nearer opaque face.  The
    resulting values are JSON primitives and can be merged into Plotly
    ``trace.meta`` without importing Plotly here.
    """

    raw_kind = _text(kind).lower().replace("-", "_")
    normalized_kind = _pick_kind(kind)
    alpha = max(0.0, min(1.0, _finite_float(opacity, 1.0)))
    if probe is None:
        probe = raw_kind.endswith("_selection") or raw_kind in {
            "selection",
            "pick",
        }
    # A marker used only for interaction is not a visual occluder even when
    # Plotly gives its trace an opaque alpha internally.
    if occludes is None:
        occludes = bool(not probe and alpha >= 1.0 - PICK_OPACITY_EPSILON)
    tolerance = PICK_TOLERANCE_PX.get(normalized_kind, 4.0)
    if tolerance_px is not None:
        tolerance = max(0.0, _finite_float(tolerance_px, tolerance))
    return {
        "mv_pick_kind": normalized_kind,
        "mv_pick_priority": int(PICK_KIND_PRIORITY.get(normalized_kind, 0)),
        "mv_pick_occludes": bool(occludes),
        "mv_pick_probe": bool(probe),
        "mv_pick_tolerance_px": float(tolerance),
        "mv_pick_opacity": float(alpha),
        "pickable": bool(pickable),
    }


def _raw_trace(point: Mapping[str, Any]) -> Mapping[str, Any]:
    trace = point.get("data") or point.get("fullData") or point.get("trace")
    return trace if isinstance(trace, Mapping) else {}


def _mesh_face_id(point: Mapping[str, Any], meta: Mapping[str, Any]) -> str | None:
    """Resolve a Mesh3d face/vertex ordinal against stable metadata."""

    face_ids = meta.get("face_ids") if isinstance(meta.get("face_ids"), (list, tuple)) else []
    vertex_ids = meta.get("vertex_face_ids") if isinstance(meta.get("vertex_face_ids"), (list, tuple)) else []
    has_face_ordinal = any(
        point.get(key) not in (None, "") for key in ("faceNumber", "faceIndex")
    )
    raw_ordinal = next(
        (point.get(key) for key in ("faceNumber", "faceIndex", "pointNumber", "pointIndex") if point.get(key) not in (None, "")),
        None,
    )
    try:
        ordinal = int(raw_ordinal)
    except (TypeError, ValueError, OverflowError):
        return None
    ids = face_ids if has_face_ordinal or not vertex_ids else vertex_ids
    if 0 <= ordinal < len(ids) and ids[ordinal] not in (None, ""):
        return str(ids[ordinal])
    return None


def _decode_custom_target(customdata: Any) -> dict[str, Any] | None:
    """Decode the compact customdata schema emitted by selection traces."""

    if not isinstance(customdata, (list, tuple)) or not customdata:
        return None
    kind = _pick_kind(customdata[0])
    if kind == "atom":
        atom_id = customdata[6] if len(customdata) > 6 else ""
        value: dict[str, Any] = {
            "kind": "atom",
            "id": atom_id,
        }
        if len(customdata) > 2:
            value["label"] = customdata[2]
        if len(customdata) > 3:
            value["element"] = customdata[3]
        if len(customdata) > 5:
            value["fragment_label"] = customdata[5]
        if len(customdata) > 7:
            value["source_index"] = customdata[7]
        if len(customdata) > 8:
            value["image_shift"] = customdata[8]
        # Older selection traces carried the source token only in the stable
        # ID (``atom:source:42``).  Recover it here so browser hit adapters
        # expose the same source/symmetry metadata regardless of trace
        # revision; newer traces still win when an explicit field is present.
        if value.get("source_index") in (None, "") and isinstance(atom_id, str):
            marker = "|source:"
            if marker in atom_id:
                source = atom_id.split(marker, 1)[1].split("|", 1)[0]
                if source:
                    value["source_index"] = source
            elif atom_id.startswith("atom:source:"):
                source = atom_id[len("atom:source:") :].split("|", 1)[0]
                if source:
                    value["source_index"] = source
        return normalize_target(value)
    if kind == "bond":
        value = {
            "kind": "bond",
            "id": customdata[4] if len(customdata) > 4 else "",
        }
        if len(customdata) > 1:
            value["label"] = customdata[1]
        if len(customdata) > 2:
            value["element"] = customdata[2]
        return normalize_target(value)
    if kind == "polyhedron":
        value = {
            "kind": "polyhedron",
            "id": customdata[4] if len(customdata) > 4 else "",
            "spec_id": customdata[1] if len(customdata) > 1 else "",
        }
        if len(customdata) > 2:
            value["fragment_label"] = customdata[2]
        if len(customdata) > 5:
            value["source_index"] = customdata[5]
        if len(customdata) > 6:
            value["image_shift"] = customdata[6]
        return normalize_target(value)
    if kind == "facet":
        return normalize_target(
            {
                "kind": "facet",
                "id": customdata[1] if len(customdata) > 1 else "",
                "miller": customdata[2] if len(customdata) > 2 else None,
                "parent_id": "morphology:bfdh-morphology",
            }
        )
    return None


def adapt_plotly_hit(point: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Adapt one Plotly point event to a normalized hit candidate.

    The adapter accepts both a point's ``data`` and ``fullData`` trace shape,
    tolerates missing depth (older Plotly versions), and preserves explicit
    ``meta`` fields.  It does not choose among candidates; call
    :func:`resolve_hit_candidate` for that step.
    """

    if not isinstance(point, Mapping):
        return None
    trace = _raw_trace(point)
    meta = trace.get("meta") if isinstance(trace.get("meta"), Mapping) else {}
    target_value = _decode_custom_target(point.get("customdata"))
    if target_value is None:
        geometry_id = meta.get("geometry_id") or meta.get("id")
        role = _pick_kind(meta.get("mv_pick_kind") or meta.get("mv_role") or meta.get("kind"))
        subentity = point.get("face_id") or point.get("subentity_id") or _mesh_face_id(point, meta)
        if role in {"geometry_entity", "geometry", "mesh3d"} and geometry_id:
            target_value = normalize_target(
                {
                    "kind": "geometry",
                    "id": geometry_id,
                    "subentity_id": subentity,
                }
            )
        elif role in {"polyhedron", "facet", "morphology"}:
            entity_id = subentity or meta.get("instance_id") or meta.get("geometry_id")
            if entity_id:
                target_value = normalize_target({"kind": "facet" if role in {"facet", "morphology"} else "polyhedron", "id": entity_id})
    if target_value is None:
        return None
    raw_role = meta.get("mv_role") or meta.get("mv_pick_kind") or target_value.get("kind")
    role = _pick_kind(raw_role)
    marker = trace.get("marker") if isinstance(trace.get("marker"), Mapping) else {}
    # Trace/marker opacity is the rendered value and wins over the default
    # policy hint.  ``mv_pick_opacity`` remains useful for adapters that only
    # receive metadata (for example a cached event fixture).
    if "opacity" in marker:
        opacity = marker.get("opacity")
    elif "opacity" in trace:
        opacity = trace.get("opacity")
    else:
        opacity = meta.get("mv_pick_opacity", 1.0)
    declared_occludes = meta.get("mv_pick_occludes")
    # Older/cached atom traces may carry the default opaque hint while their
    # live marker/mesh opacity was reduced by a style-only update.  A rendered
    # transparent trace must not retain that stale occlusion bit; an explicit
    # ``False`` (selection probe) is always honoured.
    if declared_occludes is True and _finite_float(opacity, 1.0) < 1.0 - PICK_OPACITY_EPSILON:
        declared_occludes = None
    policy = pick_trace_metadata(
        raw_role or target_value.get("kind", ""),
        opacity=opacity,
        pickable=meta.get("pickable", True),
        occludes=declared_occludes,
        probe=meta.get("mv_pick_probe"),
        tolerance_px=meta.get("mv_pick_tolerance_px"),
    )
    depth = point.get("depth", point.get("camera_depth", point.get("distance")))
    distance = point.get("pixel_distance", point.get("screen_distance", point.get("distance_px")))
    return {
        "target": target_value,
        "kind": target_value.get("kind", role),
        "id": target_value.get("id", ""),
        "depth": _finite_float(depth, math.inf),
        "pixel_distance": max(0.0, _finite_float(distance, math.inf)),
        "trace_order": _order_value(point.get("curveNumber", point.get("trace_index"))),
        "point_number": _order_value(point.get("pointNumber", point.get("pointIndex"))),
        **policy,
    }


def _candidate(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, Mapping):
        return None
    if "target" in candidate and isinstance(candidate.get("target"), Mapping):
        target_value = normalize_target(candidate["target"])
    else:
        target_value = normalize_target(candidate)
    if target_value is None:
        # A raw Plotly point is a useful input to the pure resolver too.
        return adapt_plotly_hit(candidate)
    kind = _pick_kind(candidate.get("kind") or target_value.get("kind"))
    opacity = candidate.get("opacity", candidate.get("mv_pick_opacity", 1.0))
    policy = pick_trace_metadata(
        kind,
        opacity=opacity,
        pickable=candidate.get("pickable", candidate.get("mv_pickable", True)),
        occludes=candidate.get("occludes", candidate.get("mv_pick_occludes")),
        probe=candidate.get("probe", candidate.get("mv_pick_probe")),
        tolerance_px=candidate.get("tolerance_px", candidate.get("mv_pick_tolerance_px")),
    )
    result = dict(candidate)
    result.update(policy)
    result["target"] = target_value
    result["kind"] = target_value.get("kind", kind)
    result["id"] = target_value.get("id", "")
    result["depth"] = _finite_float(candidate.get("depth", candidate.get("camera_depth", math.inf)), math.inf)
    result["pixel_distance"] = max(0.0, _finite_float(candidate.get("pixel_distance", candidate.get("screen_distance", math.inf)), math.inf))
    result["trace_order"] = _finite_float(candidate.get("trace_order", math.inf), math.inf)
    result["point_number"] = _finite_float(candidate.get("point_number", math.inf), math.inf)
    return result


def rank_hit_candidates(candidates: Iterable[Any]) -> list[dict[str, Any]]:
    """Return valid candidates in deterministic front-to-back order.

    This function applies no occlusion cutoff, making it useful to inspect a
    debug candidate list.  :func:`resolve_hit_candidate` applies the opaque
    depth rule before taking the first row.
    """

    normalized = [item for raw in candidates if (item := _candidate(raw))]
    normalized = [
        item
        for item in normalized
        if item.get("pickable", True)
        and item.get("mv_pick_opacity", 1.0) > PICK_OPACITY_EPSILON
    ]
    # Camera depth is authoritative.  Probe traces and kind priority only
    # break ties within one depth bucket, avoiding frame-to-frame jitter from
    # Plotly trace order or Python hash order.
    def key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        depth = _finite_float(item.get("depth"), math.inf)
        bucket = round(depth / PICK_DEPTH_EPSILON) if math.isfinite(depth) else math.inf
        kind = _pick_kind(item.get("kind"))
        target_value = item.get("target") if isinstance(item.get("target"), Mapping) else {}
        return (
            bucket,
            bool(item.get("mv_pick_probe", False)),
            -int(item.get("mv_pick_priority", PICK_KIND_PRIORITY.get(kind, 0))),
            _finite_float(item.get("pixel_distance"), math.inf),
            _text(target_value.get("scene_id")),
            _text(target_value.get("parent_id")),
            _text(target_value.get("subentity_id")),
            _text(target_value.get("id") or item.get("id")),
            _finite_float(item.get("trace_order"), math.inf),
            _finite_float(item.get("point_number"), math.inf),
        )

    return sorted(normalized, key=key)


def resolve_hit_candidate(
    candidates: Iterable[Any],
    *,
    depth_epsilon: float = PICK_DEPTH_EPSILON,
) -> dict[str, Any] | None:
    """Choose one semantic hit while respecting opaque occlusion.

    Opaque visual geometry cuts off candidates strictly behind its front-most
    depth.  Transparent surfaces remain pickable but do not hide objects
    behind them.  Missing depth is treated as infinitely far away, and all
    ties are broken by kind priority, pixel distance, semantic identity, and
    source order.  The returned row includes the normalized ``target`` and
    policy metadata; ``None`` means every candidate was hidden or malformed.
    """

    ranked = rank_hit_candidates(candidates)
    if not ranked:
        return None
    epsilon = max(0.0, _finite_float(depth_epsilon, PICK_DEPTH_EPSILON))
    opaque = [
        item
        for item in ranked
        if bool(item.get("mv_pick_occludes")) and math.isfinite(_finite_float(item.get("depth"), math.inf))
    ]
    if not opaque:
        return ranked[0]
    nearest_opaque_depth = min(_finite_float(item.get("depth"), math.inf) for item in opaque)
    visible = [
        item
        for item in ranked
        if _finite_float(item.get("depth"), math.inf) <= nearest_opaque_depth + epsilon
    ]
    return visible[0] if visible else None


def resolve_hit(candidates: Iterable[Any], *, depth_epsilon: float = PICK_DEPTH_EPSILON) -> dict[str, Any] | None:
    """Return only the semantic target selected from ``candidates``."""

    selected = resolve_hit_candidate(candidates, depth_epsilon=depth_epsilon)
    return selected.get("target") if selected else None


# Concise aliases used by adapters and downstream applications.
choose_hit = resolve_hit
select_hit = resolve_hit
hit_priority = rank_hit_candidates


__all__ = [
    "atom_instance_id",
    "bond_instance_id",
    "ensure_polyhedron_instance_ids",
    "facet_instance_id",
    "normalize_target",
    "polyhedron_instance_id",
    "target",
    "PICK_DEPTH_EPSILON",
    "PICK_KIND_PRIORITY",
    "PICK_OPACITY_EPSILON",
    "PICK_TOLERANCE_PX",
    "adapt_plotly_hit",
    "choose_hit",
    "hit_priority",
    "pick_trace_metadata",
    "rank_hit_candidates",
    "resolve_hit",
    "resolve_hit_candidate",
    "select_hit",
]
