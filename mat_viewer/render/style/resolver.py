"""Typed, field-by-field style inheritance for scene entities.

The renderer has entity-specific paint controls (atoms, bonds, topology and
caller geometry), but selection overrides should not create another parallel
styling stack.  This small resolver deliberately works on plain mappings so it
can be used by Python, REST and export paths without mutating source geometry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from ..picking import atom_instance_id, bond_instance_id

ENTITY_FIELDS: dict[str, frozenset[str]] = {
    "atom": frozenset({"color", "opacity", "visible", "radius", "material", "representation"}),
    "bond": frozenset({"color", "opacity", "visible", "radius", "thickness", "material"}),
    "polyhedron": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width", "flatshading"}),
    "facet": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width"}),
    "geometry": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width", "flatshading", "material"}),
    "geometry_entity": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width", "flatshading", "material"}),
    "molecule": frozenset({"color", "opacity", "visible"}),
    "part": frozenset({"color", "opacity", "visible"}),
    "morphology": frozenset({"color", "opacity", "visible", "edge_color", "edge_opacity", "edge_width"}),
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
    if key in {"color", "edge_color"}:
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


def validate_style_override_patch(kind: str, patch: Mapping[str, Any]) -> None:
    """Validate an explicit style patch before it reaches backend state.

    Persisted scene state remains forgiving (``normalize_style_overrides``
    drops malformed values), but an API mutation must explain why a request
    cannot be painted.  Unlike the renderer-safe coercer, this boundary does
    not clamp out-of-range numbers or silently discard invalid fields.
    """

    normalized_kind = str(kind or "").strip().lower()
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
        elif key in {"color", "edge_color"}:
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

    return ENTITY_FIELDS.get(str(kind or "").lower(), frozenset())


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
    theme: Mapping[str, Any] | None = None,
    kind_defaults: Mapping[str, Any] | None = None,
    rules: Sequence[Mapping[str, Any]] | None = None,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve one entity using the documented precedence contract.

    Theme and kind defaults are mappings; ``rules`` apply in list order
    (later matching rules win), while ``overrides`` is keyed by canonical
    target ID.  Only fields supported by the entity kind are returned.
    """

    kind = str(entity.get("kind") or "geometry").lower()
    out = _filtered(theme or {}, kind)
    out.update(_filtered(kind_defaults or {}, kind))
    for rule in rules or ():
        if isinstance(rule, Mapping) and _rule_matches(rule, entity):
            out.update(_filtered(rule.get("style") or rule, kind))
    target_id = str(entity.get("id") or entity.get("instance_id") or "")
    local = overrides.get(target_id) if isinstance(overrides, Mapping) else None
    if isinstance(local, Mapping):
        out.update(_filtered(local, kind))
    return out


def normalize_style_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    """Validate persisted target overrides and drop unsupported/malformed data."""

    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for target_id, value in raw.items():
        if not isinstance(value, Mapping):
            continue
        kind = str(value.get("kind") or "geometry").lower()
        style = _safe_style_mapping(value, kind)
        if style:
            result[str(target_id)] = {"kind": kind, **style}
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
    current["kind"] = str(kind).lower()
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
        if isinstance(override, Mapping) and str(override.get("kind", "atom")) == "atom":
            if "color" in override:
                atom["_render_color"] = str(override["color"])
                atom["_render_color_light"] = str(override["color"])
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
        if isinstance(override, Mapping) and str(override.get("kind", "bond")) == "bond":
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
    "apply_scene_style_overrides",
    "merge_style_override",
    "normalize_style_overrides",
    "resolve_entity_style",
    "supported_style_fields",
    "validate_style_override_patch",
]
