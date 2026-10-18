"""Typed, field-by-field style inheritance for scene entities.

The renderer has entity-specific paint controls (atoms, bonds, topology and
caller geometry), but selection overrides should not create another parallel
styling stack.  This small resolver deliberately works on plain mappings so it
can be used by Python, REST and export paths without mutating source geometry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from ..picking import atom_instance_id, bond_instance_id

ENTITY_FIELDS: dict[str, frozenset[str]] = {
    # ``property_color`` is kept separate from ``color`` because a
    # continuous atom-property map can be active at the same time as an
    # explicit per-instance paint override.  Renderers use the former as the
    # property-map value and the latter as the solid override.
    "atom": frozenset(
        {
            "color",
            "property_color",
            "opacity",
            "visible",
            "radius",
            "material",
            "representation",
        }
    ),
    "bond": frozenset({"color", "opacity", "visible", "radius", "thickness", "material"}),
    "polyhedron": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width", "flatshading"}),
    "facet": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width"}),
    "geometry": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width", "flatshading", "material"}),
    "geometry_entity": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width", "flatshading", "material"}),
    "molecule": frozenset({"color", "opacity", "visible"}),
    "part": frozenset({"color", "opacity", "visible"}),
    "morphology": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width"}),
}

# Scene payloads use a few historical names for the same paintable object.
# Canonicalising only for field lookup preserves the caller's kind in the
# persisted override while ensuring that ``mesh`` and ``geometry_entity`` do
# not accidentally reject the fields accepted by generic geometry.
_KIND_ALIASES: dict[str, str] = {
    "mesh": "geometry",
    "surface": "geometry",
    "solid": "geometry",
    "generic_geometry": "geometry",
    "geometry_entity": "geometry",
    "geometry-entity": "geometry",
    "polyhedron_instance": "polyhedron",
    "polyhedron-instance": "polyhedron",
    "bfdh": "facet",
    "bfdh_facet": "facet",
    "bfdh-facet": "facet",
}

# These are intentionally renderer-safe, stable defaults.  They mirror the
# colours used by the geometry and topology builders but live here so API,
# export, and browser paths resolve a missing field identically.
KIND_DEFAULTS: dict[str, dict[str, Any]] = {
    "geometry": {
        "color": "#7C5CBF",
        "opacity": 1.0,
        "visible": True,
        "edge_color": "#7C5CBF",
        "edge_opacity": 1.0,
        "edge_width": 2.0,
        "flatshading": True,
    },
    "polyhedron": {
        "color": "#7C5CBF",
        "opacity": 0.55,
        "visible": True,
        "edge_color": "#7C5CBF",
        "edge_opacity": 0.90,
        "edge_width": 3.0,
        "flatshading": True,
    },
    "facet": {
        "color": "#4f7cff",
        "opacity": 0.80,
        "visible": True,
        "edge_color": "#4f7cff",
        "edge_opacity": 1.0,
        "edge_width": 2.0,
    },
    "morphology": {
        "color": "#4f7cff",
        "opacity": 0.80,
        "visible": True,
        "edge_color": "#4f7cff",
        "edge_opacity": 1.0,
        "edge_width": 2.0,
    },
}

# Named themes are deliberately small: callers can still pass a mapping for
# exact control.  A theme changes only defaults, so explicit kind/rule/
# instance values retain the documented precedence.
THEME_DEFAULTS: dict[str, dict[str, Any]] = {
    "light": {"color": "#7C5CBF", "edge_color": "#7C5CBF"},
    "dark": {"color": "#B794F4", "edge_color": "#D6BCFA"},
    "canonical": {"color": "#7C5CBF", "edge_color": "#7C5CBF"},
    "default": {"color": "#7C5CBF", "edge_color": "#7C5CBF"},
}

_OPACITY_FIELDS = frozenset({"opacity", "edge_opacity"})
_NON_NEGATIVE_FIELDS = frozenset({"radius", "thickness", "edge_width"})
_MATERIAL_VALUES = frozenset({"mesh", "flat"})
_REPRESENTATION_VALUES = frozenset(
    {"ball", "ball_stick", "space_filling", "stick", "ortep", "wireframe"}
)
_BASIC_RENDER_COLORS = frozenset(
    {
        "black",
        "white",
        "red",
        "green",
        "blue",
        "gray",
        "grey",
        "yellow",
        "orange",
        "purple",
    }
)


def _is_render_color(value: Any) -> bool:
    """Return whether ``value`` is understood by the render colour parser.

    Keep style validation independent of Plotly.  The low-level geometry
    parser accepts the compact CSS subset that all MatterVis renderers share
    (hex colours, including alpha, and a handful of basic colour names).  A
    malformed value must be dropped before it reaches a Plotly trace: Plotly
    validates lazily and otherwise turns a bad persisted override into a
    callback/render crash.
    """

    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.lower() in _BASIC_RENDER_COLORS:
        return True
    # Require the hash marker for hexadecimal values.  The low-level parser
    # is intentionally permissive here for legacy callers, but Plotly's CSS
    # validator is not: accepting bare ``abcdef`` would defer the failure to
    # trace construction.
    if not text.startswith("#"):
        return False
    text = text[1:]
    if len(text) not in (3, 4, 6, 8):
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True


def _finite_number(value: Any) -> float | None:
    """Coerce a style number without allowing NaN, infinity, or booleans."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _safe_style_value(key: str, value: Any) -> Any:
    """Return a renderer-safe value, or ``None`` for an invalid field.

    Overrides arrive through both REST and persisted state, so renderers must
    not assume that a caller already validated them.  Opacities are bounded
    (rather than allowed to reach Plotly's strict validator); dimensions are
    required to be finite and non-negative.  Invalid values are omitted by
    the caller, preserving the inherited style.
    """

    if key in _OPACITY_FIELDS:
        number = _finite_number(value)
        return None if number is None else max(0.0, min(1.0, number))
    if key in _NON_NEGATIVE_FIELDS:
        number = _finite_number(value)
        return None if number is None or number < 0.0 else number
    if key == "visible":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "on"}:
                return True
            if text in {"false", "0", "no", "off"}:
                return False
        return None
    if key in {"color", "property_color", "edge_color"}:
        text = value.strip() if isinstance(value, str) else ""
        return text if _is_render_color(text) else None
    if key == "material":
        if not isinstance(value, str):
            return None
        text = value.strip().lower()
        return text if text in _MATERIAL_VALUES else None
    if key == "representation":
        if not isinstance(value, str):
            return None
        text = value.strip().lower().replace("-", "_")
        return text if text in _REPRESENTATION_VALUES else None
    if key == "flatshading":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "on"}:
                return True
            if text in {"false", "0", "no", "off"}:
                return False
        return None
    return deepcopy(value)


def _safe_style_mapping(values: Mapping[str, Any], kind: str) -> dict[str, Any]:
    fields = supported_style_fields(kind)
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key not in fields:
            continue
        safe = _safe_style_value(key, value)
        if safe is not None:
            result[key] = safe
    return result


def _canonical_kind(kind: Any) -> str:
    """Return the field-contract kind for a scene/entity kind."""

    normalized = str(kind or "geometry").strip().lower().replace(" ", "_")
    return _KIND_ALIASES.get(normalized, normalized)


def _mapping_for_kind(values: Mapping[str, Any] | None, kind: str) -> Mapping[str, Any]:
    """Extract a style mapping that may be global or keyed by entity kind.

    Public callers historically passed a flat mapping (``{"color": ...}``),
    while scene-level theme/default payloads often use
    ``{"polyhedron": {"color": ...}, "geometry": {...}}``.  Supporting both
    forms here avoids each renderer inventing a different merge rule.
    """

    if not isinstance(values, Mapping):
        return {}
    canonical = _canonical_kind(kind)
    nested = values.get(kind)
    if nested is None:
        nested = values.get(canonical)
    if nested is None:
        nested = values.get("*")
    if isinstance(nested, Mapping):
        # Flat fields remain useful as a shared base for all kinds.
        flat = {key: value for key, value in values.items() if key in ENTITY_FIELDS.get(canonical, frozenset())}
        merged = dict(flat)
        merged.update(nested)
        return merged
    return values


def _theme_mapping(theme: Mapping[str, Any] | str | None, kind: str) -> Mapping[str, Any]:
    """Resolve a named theme or a caller-provided style mapping."""

    if isinstance(theme, Mapping):
        # ``{"name": "dark", ...}`` is accepted for JSON callers while
        # preserving explicit fields over the named theme defaults.
        name = theme.get("name") or theme.get("theme")
        base = dict(THEME_DEFAULTS.get(str(name).lower(), {})) if name else {}
        base.update(_mapping_for_kind(theme, kind))
        return base
    if theme is None:
        return {}
    return dict(THEME_DEFAULTS.get(str(theme).strip().lower(), {}))


def _default_style_for_kind(kind: str, entity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a copy of stable defaults for one entity kind.

    Atom colours are resolved from the canonical element palette only when an
    element is present.  This keeps generic geometry defaults independent of
    chemistry while giving the style resolver the same atom fallback as the
    regular renderer.
    """

    canonical = _canonical_kind(kind)
    result = dict(KIND_DEFAULTS.get(canonical, {}))
    if canonical == "atom" and isinstance(entity, Mapping):
        element = entity.get("elem") or entity.get("element") or entity.get("symbol")
        if element:
            try:
                from ...config import element_color

                result["color"] = element_color(str(element))
            except (ImportError, ValueError):
                pass
    return result


def validate_style_override_patch(kind: str, patch: Mapping[str, Any]) -> None:
    """Validate an explicit style patch before it reaches backend state.

    Persisted scene state remains forgiving (``normalize_style_overrides``
    drops malformed values), but an API mutation must explain why a request
    cannot be painted.  Unlike the renderer-safe coercer, this boundary does
    not clamp out-of-range numbers or silently discard invalid fields.
    """

    normalized_kind = _canonical_kind(kind)
    fields = supported_style_fields(normalized_kind)
    if not fields:
        raise ValueError(f"unknown style target kind: {kind!r}")
    if not isinstance(patch, Mapping) or not patch:
        raise ValueError("style patch must contain at least one field")
    unknown = sorted(str(key) for key in patch if key not in fields)
    if unknown:
        raise ValueError(
            f"unsupported style field(s) for {normalized_kind}: {', '.join(unknown)}"
        )
    for key, value in patch.items():
        if key in _OPACITY_FIELDS:
            number = _finite_number(value)
            if number is None or not 0.0 <= number <= 1.0:
                raise ValueError(f"{key} must be a finite number in [0, 1]")
        elif key in _NON_NEGATIVE_FIELDS:
            number = _finite_number(value)
            if number is None or number < 0.0:
                raise ValueError(f"{key} must be a finite non-negative number")
        elif key == "visible":
            if not isinstance(value, bool):
                raise ValueError("visible must be a boolean")
        elif key in {"color", "property_color", "edge_color"}:
            if not _is_render_color(value):
                raise ValueError(f"{key} must be a renderer-safe color")
        elif key == "material":
            if not isinstance(value, str) or value.strip().lower() not in _MATERIAL_VALUES:
                raise ValueError("material must be 'mesh' or 'flat'")
        elif key == "representation":
            normalized = str(value).strip().lower().replace("-", "_") if isinstance(value, str) else ""
            if normalized not in _REPRESENTATION_VALUES:
                raise ValueError("unsupported atom representation")
        elif key == "flatshading":
            if not isinstance(value, bool):
                raise ValueError("flatshading must be a boolean")


def supported_style_fields(kind: str) -> frozenset[str]:
    """Return properties that the selected entity kind can actually paint."""

    return ENTITY_FIELDS.get(_canonical_kind(kind), frozenset())


def style_fields_for_targets(targets: Iterable[Any]) -> frozenset[str]:
    """Return the field intersection paintable by every selected target.

    ``targets`` may contain canonical target mappings (``{"kind": ...}``),
    plain kind strings, or mappings with a nested ``target``/``entity``.  An
    empty selection returns an empty set, which lets UI callers disable the
    override controls without guessing at a default entity type.
    """

    if isinstance(targets, Mapping):
        values = [targets]
    elif isinstance(targets, (str, bytes)):
        values = [targets]
    else:
        values = list(targets or [])
    common: frozenset[str] | None = None
    for value in values:
        if isinstance(value, Mapping):
            nested = value.get("target") or value.get("entity")
            kind = nested.get("kind") if isinstance(nested, Mapping) else value.get("kind")
        else:
            kind = value
        fields = supported_style_fields(str(kind or "geometry"))
        common = fields if common is None else common.intersection(fields)
    return common or frozenset()


# Descriptive aliases used by browser/API callers.  Keep one implementation
# so parent/child and multi-selection behavior cannot diverge by entry point.
supported_style_fields_for_selection = style_fields_for_targets
common_style_fields = style_fields_for_targets


def _rule_matches(rule: Mapping[str, Any], entity: Mapping[str, Any]) -> bool:
    selector = rule.get("selector") or rule.get("target") or {}
    if not isinstance(selector, Mapping):
        return False
    for key, expected in selector.items():
        if key in {"all", "any"} and bool(expected):
            continue
        actual = entity.get(key)
        if isinstance(expected, (list, tuple, set, frozenset)):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def _filtered(values: Mapping[str, Any], kind: str) -> dict[str, Any]:
    return _safe_style_mapping(values, kind)


def resolve_entity_style(
    entity: Mapping[str, Any],
    *,
    theme: Mapping[str, Any] | str | None = None,
    kind_defaults: Mapping[str, Any] | None = None,
    rules: Sequence[Mapping[str, Any]] | None = None,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve one entity using the documented precedence contract.

    Theme and kind defaults may be flat mappings or mappings keyed by entity
    kind.  A named theme (``"light"``/``"dark"``/``"canonical"``) is also
    accepted.  ``rules`` apply in list order (later matching rules win),
    while ``overrides`` is keyed by canonical target ID.  A child entity may
    inherit a parent override through ``parent_id``/``parent_target_id``;
    child fields are then applied last.  Only fields supported by the entity
    kind are returned.
    """

    kind = str(entity.get("kind") or "geometry").lower()
    # Start with renderer defaults, then theme, then explicit kind defaults.
    # This makes a theme switch repaint the same scene while preserving the
    # caller's field-wise precedence contract.
    out = _filtered(_default_style_for_kind(kind, entity), kind)
    out.update(_filtered(_theme_mapping(theme, kind), kind))
    out.update(_filtered(_mapping_for_kind(kind_defaults, kind), kind))
    for rule in rules or ():
        if isinstance(rule, Mapping) and _rule_matches(rule, entity):
            out.update(_filtered(rule.get("style") or rule, kind))
    if isinstance(overrides, Mapping):
        # Parent/scope layers are applied first.  Exact child overrides always
        # win, and unrelated siblings remain isolated because only explicit
        # IDs in this chain are considered.
        target_ids: list[str] = []
        for key in (
            "scope_id",
            "parent_id",
            "parent_target_id",
            "parent_instance_id",
        ):
            value = entity.get(key)
            if isinstance(value, Mapping):
                value = value.get("id") or value.get("target_id")
            if value not in (None, ""):
                target_ids.append(str(value))
        parent = entity.get("parent")
        if isinstance(parent, Mapping):
            value = parent.get("id") or parent.get("target_id")
            if value not in (None, ""):
                target_ids.append(str(value))
        target_id = str(entity.get("id") or entity.get("instance_id") or "")
        if target_id:
            target_ids.append(target_id)
        seen: set[str] = set()
        for candidate in target_ids:
            if candidate in seen:
                continue
            seen.add(candidate)
            local = overrides.get(candidate)
            if not isinstance(local, Mapping):
                continue
            style = local.get("style") if isinstance(local.get("style"), Mapping) else local
            out.update(_filtered(style, kind))
    return out


def normalize_style_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    """Validate persisted target overrides and drop unsupported/malformed data."""

    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for target_id, value in raw.items():
        if not isinstance(value, Mapping):
            continue
        kind = str(value.get("kind") or "geometry").strip().lower()
        nested = value.get("style") if isinstance(value.get("style"), Mapping) else value
        style = _safe_style_mapping(nested, kind)
        if style:
            item: dict[str, Any] = {"kind": kind, **style}
            # Scope is metadata rather than a paint field. Preserve the small
            # documented vocabulary so parent/scene overrides survive a
            # state round-trip without allowing arbitrary persisted keys.
            scope = value.get("scope")
            if isinstance(scope, str) and scope.strip().lower() in {
                "scene",
                "parent",
                "instance",
                "selection",
                "global",
            }:
                item["scope"] = scope.strip().lower()
            result[str(target_id)] = item
    return result


def merge_style_override(
    overrides: Mapping[str, Mapping[str, Any]] | None,
    target_id: str,
    kind: str,
    patch: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return a copy with a validated target patch (no-op fields are omitted)."""

    validate_style_override_patch(kind, patch)
    # Re-normalise the existing mapping as well.  This protects callers that
    # bypass the backend state reducer and hand a persisted mapping directly
    # to the renderer.
    result = normalize_style_overrides(overrides)
    fields = supported_style_fields(kind)
    current = dict(result.get(str(target_id)) or {})
    current["kind"] = str(kind).strip().lower()
    for key, value in patch.items():
        if key not in fields:
            continue
        value = _safe_style_value(key, value)
        if value is None:
            continue
        if current.get(key) == value:
            continue
        current[key] = value
    if len(current) > 1:
        result[str(target_id)] = current
    else:
        result.pop(str(target_id), None)
    return result


def merge_style_overrides_for_targets(
    overrides: Mapping[str, Mapping[str, Any]] | None,
    targets: Iterable[Mapping[str, Any] | str],
    patch: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Apply one field-wise patch to a multi-selection atomically.

    The patch must be supported by every selected kind.  This prevents a
    parent/child selection from silently applying (for example) ``radius`` to
    the atom while dropping it from the polyhedron, and gives browser clients
    one deterministic intersection to use for their controls.
    """

    values = list(targets or [])
    common = style_fields_for_targets(values)
    if not common:
        raise ValueError("selection has no common style fields")
    if not isinstance(patch, Mapping) or not patch:
        raise ValueError("style patch must contain at least one field")
    unknown = sorted(str(key) for key in patch if key not in common)
    if unknown:
        raise ValueError(
            "unsupported style field(s) for selection: " + ", ".join(unknown)
        )
    result = normalize_style_overrides(overrides)
    for value in values:
        if isinstance(value, Mapping):
            target_id = value.get("id") or value.get("target_id") or value.get("instance_id")
            nested = value.get("target")
            kind = value.get("kind") or (nested.get("kind") if isinstance(nested, Mapping) else None)
        else:
            target_id, kind = value, "geometry"
        if target_id in (None, ""):
            continue
        result = merge_style_override(result, str(target_id), str(kind or "geometry"), patch)
    return result


# Short alias for API adapters that model a selected set as an operation.
merge_selection_style_override = merge_style_overrides_for_targets


def apply_scene_style_overrides(
    scene: Mapping[str, Any], style: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return a scene copy with exact atom/bond overrides materialized.

    Interactive targets use the same stable instance IDs as the picking
    traces.  Materializing the small set of paint fields onto the displayed
    atom/bond records keeps every renderer (mesh, scatter, wireframe and
    ORTEP) on one override path without mutating the loader-owned scene.
    """

    raw = style.get("style_overrides") if isinstance(style, Mapping) else None
    overrides = normalize_style_overrides(raw)
    if not overrides:
        return dict(scene)
    output = dict(scene)
    atoms = []
    for index, value in enumerate(scene.get("draw_atoms") or []):
        atom = dict(value)
        override = overrides.get(atom_instance_id(atom, index))
        if isinstance(override, Mapping) and _canonical_kind(override.get("kind", "atom")) == "atom":
            if "color" in override:
                atom["_render_color"] = str(override["color"])
                atom["_render_color_light"] = str(override["color"])
            if "property_color" in override:
                # A property-map override is intentionally independent from a
                # solid ``color`` override.  Resetting this field removes the
                # persisted key and lets the next scene rebuild restore the
                # continuous map's original value.
                atom["_property_color"] = str(override["property_color"])
                atom["_property_color_override"] = True
            if "opacity" in override:
                try:
                    atom["_render_opacity_scale"] = max(0.0, min(1.0, float(override["opacity"])))
                except (TypeError, ValueError):
                    pass
            if "visible" in override:
                atom["_render_visible"] = bool(override["visible"])
            if "radius" in override:
                try:
                    radius = float(override["radius"])
                    if radius > 0:
                        atom["atom_radius"] = radius
                except (TypeError, ValueError):
                    pass
        atoms.append(atom)
    output["draw_atoms"] = atoms

    bond_radius = 0.15
    if isinstance(style, Mapping):
        try:
            bond_radius = max(float(style.get("bond_radius", bond_radius)), 1.0e-9)
        except (TypeError, ValueError):
            pass
    bonds = []
    for index, value in enumerate(scene.get("bonds") or []):
        bond = dict(value)
        override = overrides.get(bond_instance_id(bond, atoms, index))
        if isinstance(override, Mapping) and _canonical_kind(override.get("kind", "bond")) == "bond":
            if "color" in override:
                bond["_render_color"] = str(override["color"])
            if "opacity" in override:
                try:
                    bond["_render_opacity_scale"] = max(0.0, min(1.0, float(override["opacity"])))
                except (TypeError, ValueError):
                    pass
            if "visible" in override:
                bond["_render_visible"] = bool(override["visible"])
            for field in ("radius", "thickness"):
                if field in override:
                    try:
                        bond["_render_radius_scale"] = max(0.0, float(override[field]) / bond_radius)
                    except (TypeError, ValueError):
                        pass
        bonds.append(bond)
    output["bonds"] = bonds
    return output


__all__ = [
    "ENTITY_FIELDS",
    "KIND_DEFAULTS",
    "THEME_DEFAULTS",
    "apply_scene_style_overrides",
    "common_style_fields",
    "merge_style_override",
    "merge_style_overrides_for_targets",
    "merge_selection_style_override",
    "normalize_style_overrides",
    "resolve_entity_style",
    "style_fields_for_targets",
    "supported_style_fields",
    "supported_style_fields_for_selection",
    "validate_style_override_patch",
]
