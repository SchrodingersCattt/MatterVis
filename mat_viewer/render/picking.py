"""Stable semantic identities shared by renderers and browser picking.

Plotly trace indexes and atom labels are presentation details.  They are not
safe selection keys: a label can occur in a periodic image, a batched mesh can
contain many independent objects, and redraws are free to reorder traces.  The
helpers in this module build a small JSON-safe target contract that is used by
interaction traces, server state, and the browser controller.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


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
    disorder = (
        atom.get("_disorder_part")
        or atom.get("disorder_part")
        or atom.get("_disorder_group")
        or atom.get("disorder_group")
        or atom.get("dg")
    )
    parts = [f"source:{_token(source)}"]
    if image not in (None, "", (), [], [0, 0, 0]):
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


__all__ = [
    "atom_instance_id",
    "bond_instance_id",
    "ensure_polyhedron_instance_ids",
    "facet_instance_id",
    "normalize_target",
    "polyhedron_instance_id",
    "target",
]
