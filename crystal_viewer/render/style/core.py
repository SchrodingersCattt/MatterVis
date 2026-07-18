from __future__ import annotations
# ruff: noqa: F401,F403,F405

from ..common import *

def validate_style_schema(style: dict) -> dict:
    material = str(style.get("material", "mesh"))
    render_style = str(style.get("style", "ball_stick"))
    disorder = str(style.get("disorder", "outline_rings"))
    ortep_mode = style.get("ortep_mode")
    ortep_mode_minor = style.get("ortep_mode_minor")
    projection = str(style.get("projection", "perspective"))
    if material not in MATERIAL_DISPATCH:
        raise ValueError(f"unknown material: {material}")
    if render_style not in STYLE_DISPATCH:
        raise ValueError(f"unknown style: {render_style}")
    if disorder not in DISORDER_DISPATCH:
        raise ValueError(f"unknown disorder mode: {disorder}")
    if ortep_mode is not None and str(ortep_mode) not in ORTEP_MODES:
        raise ValueError(f"unknown ORTEP mode: {ortep_mode}")
    if ortep_mode_minor is not None and str(ortep_mode_minor) not in ORTEP_MODES:
        raise ValueError(f"unknown minor ORTEP mode: {ortep_mode_minor}")
    if projection not in ("perspective", "orthographic"):
        raise ValueError(f"unknown projection: {projection}")
    normalized = dict(style)
    normalized["material"] = material
    normalized["style"] = render_style
    normalized["disorder"] = disorder
    normalized["projection"] = projection
    normalized["camera_eye_distance"] = float(normalized.get("camera_eye_distance", 1.8))
    if ortep_mode is not None:
        normalized["ortep_mode"] = str(ortep_mode)
        normalized.update(ORTEP_MODES[normalized["ortep_mode"]])
    if ortep_mode_minor is not None:
        normalized["ortep_mode_minor"] = str(ortep_mode_minor)
    normalized["fast_rendering"] = bool(normalized.get("fast_rendering", False)) or material == "flat"
    normalized["minor_wireframe"] = bool(normalized.get("minor_wireframe", False)) or disorder == "outline_rings"
    return normalized


def _minor_opacity_for(style: dict, is_minor: bool) -> float:
    return minor_opacity_for(style, is_minor)


def _stamp_trace(
    trace,
    *,
    role: str,
    is_minor: bool | None = None,
    hide_on_minor_only: bool = False,
    visible: bool | None = None,
):
    meta = dict(getattr(trace, "meta", None) or {})
    meta["mv_role"] = role
    if is_minor is not None:
        meta["mv_minor"] = bool(is_minor)
    if hide_on_minor_only:
        meta["mv_hide_on_minor_only"] = True
    trace.meta = meta
    if visible is not None:
        trace.visible = bool(visible)
    return trace


def _style_color(color: str, style: dict) -> str:
    """Apply the legacy ``monochrome`` flag.

    When ``style["atom_groups"]`` is non-empty the monochrome flag is
    treated as inert: atom_groups is the single source of truth and
    double-applying ``monochrome`` would surprise users who set up an
    explicit colour rule but expected unmatched atoms to keep their
    element palette. A backend caller that wants "everything black"
    should add ``{"selector": {"all": True}, "color": "#000000"}`` to
    atom_groups -- the migration in ``ViewerBackend.normalize_state``
    does this automatically when an old preset was loaded with
    ``monochrome=True``.
    """
    if style.get("atom_groups"):
        return color
    return "#000000" if style.get("monochrome", False) else color


def _atom_render_color(atom: dict, style: dict, *, light: bool = False) -> str:
    """Resolve an atom's effective render colour after Phase 2
    atom_groups overrides.

    - ``atom["_render_color"]`` (or ``_render_color_light`` for the
      minor / light path) wins when set by a matching group rule.
    - Otherwise we fall back to the element-palette colour passed
      through :func:`_style_color`. The legacy ``monochrome`` flag
      only takes effect when no atom_groups are set on the scene.
    """
    field = "_render_color_light" if light else "_render_color"
    override = atom.get(field)
    if override:
        return str(override)
    base = atom.get("color_light" if light else "color", "#888888")
    return _style_color(base, style)


def _atom_render_visible(atom: dict) -> bool:
    return bool(atom.get("_render_visible", True))


def _atom_render_opacity_scale(atom: dict) -> float:
    try:
        return max(0.0, min(1.0, float(atom.get("_render_opacity_scale", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def _atom_effective_opacity(atom: dict, style: dict) -> float:
    """Resolve an atom's final opacity after Phase 2 atom_groups overrides.

    Replace semantics: when an atom_group rule supplies an explicit
    opacity (i.e. ``_render_opacity_scale`` was set to anything other
    than the default 1.0), we use that value directly and IGNORE the
    disorder/minor fade for this atom. Otherwise we fall back to the
    legacy per-style fade (``_minor_opacity_for``).

    Stacking semantics (multiplicative) caused minor + group=0.5 atoms
    to drift to ~0.18, which read as "disappearing" rather than
    "halved" -- and a user setting opacity=0 expects an invisible atom,
    not "0 × something".

    When ``disorder == "opacity"``, partial-occupancy atoms use their
    crystallographic occupancy as opacity (occ=0.526 → alpha=0.526).
    """
    # Explicit atom_group override takes priority over everything.
    scale = atom.get("_render_opacity_scale", 1.0)
    try:
        scale_f = max(0.0, min(1.0, float(scale)))
    except (TypeError, ValueError):
        scale_f = 1.0
    if scale_f < 0.999:
        return scale_f

    # In disorder="opacity" mode, use crystallographic occupancy directly.
    if style.get("disorder") == "opacity" or style.get("force_minor_fade"):
        occ = atom.get("occ", 1.0)
        try:
            occ_f = float(occ)
        except (TypeError, ValueError):
            occ_f = 1.0
        if occ_f < 0.999:
            return max(0.05, occ_f)

    # Full-occupancy atoms or non-opacity disorder modes.
    is_minor = bool(atom.get("is_minor", False))
    return _minor_opacity_for(style, is_minor)


def _atom_opacity_group_id(atom: dict) -> str | None:
    group_id = atom.get("_render_opacity_group_id")
    if group_id is None:
        return None
    text = str(group_id)
    return text or None


def _bond_opacity_group_id(bond: dict) -> str | None:
    group_id = bond.get("_render_opacity_group_id")
    if group_id is None:
        return None
    text = str(group_id)
    return text or None


def _latency_meta(
    role: str,
    *,
    is_minor: bool | None = None,
    opacity_group: str | None = None,
    opacity_scale: float | None = None,
) -> dict:
    meta = {"mv_role": role}
    if is_minor is not None:
        meta["mv_minor"] = bool(is_minor)
    if opacity_group:
        meta["mv_opacity_group"] = str(opacity_group)
    if opacity_scale is not None:
        meta["mv_opacity_scale"] = float(opacity_scale)
    return meta


def _annotate_trace(
    trace,
    role: str,
    *,
    is_minor: bool | None = None,
    opacity_group: str | None = None,
    opacity_scale: float | None = None,
):
    if trace is not None:
        trace.update(
            meta=_latency_meta(
                role,
                is_minor=is_minor,
                opacity_group=opacity_group,
                opacity_scale=opacity_scale,
            )
        )
    return trace


def _style_trace_dicts(trace_dicts: list[dict], style: dict) -> list[dict]:
    """Apply style-only visibility/opacity to cached trace dictionaries.

    The geometry cache intentionally ignores controls such as
    ``show_minor_only`` and opacity sliders. Those controls are cheap
    trace-property edits, so replay cached vertex arrays and stamp the
    current visible/opacity values onto shallow copies.
    """
    show_minor_only = bool(style.get("show_minor_only", False))
    show_labels = bool(style.get("show_labels", True))
    show_axes = bool(style.get("show_axes", True))
    show_unit_cell = bool(style.get("show_unit_cell", False))
    atom_group_opacity = {
        str(group.get("id") or ""): float(group.get("opacity"))
        for group in (style.get("atom_groups") or [])
        if group.get("id") and group.get("opacity") is not None
    }
    bond_group_opacity = {
        str(group.get("id") or ""): float(group.get("opacity"))
        for group in (style.get("bond_groups") or [])
        if group.get("id") and group.get("opacity") is not None
    }
    out: list[dict] = []
    for trace in trace_dicts:
        copied = dict(trace)
        meta = copied.get("meta") if isinstance(copied.get("meta"), dict) else {}
        role = meta.get("mv_role")
        is_minor = bool(meta.get("mv_minor", False))
        if role == "labels":
            copied["visible"] = show_labels and (not show_minor_only or is_minor)
        elif role == "axes":
            copied["visible"] = show_axes
        elif role == "unit_cell":
            copied["visible"] = show_unit_cell
        elif show_minor_only and role in {"atom", "bond", "atom_selection", "bond_selection"} and not is_minor:
            copied["visible"] = False
        elif role in {"atom", "bond", "atom_selection", "bond_selection"}:
            copied["visible"] = True
        group_id = meta.get("mv_opacity_group")
        if role == "atom":
            if str(group_id) in atom_group_opacity:
                opacity = atom_group_opacity[str(group_id)]
            else:
                # Preserve the trace's own opacity (set by _atom_mesh_traces
                # using _atom_effective_opacity which respects occ in
                # disorder='opacity' mode).  Only override when absent.
                opacity = copied.get("opacity") if copied.get("opacity") is not None else _minor_opacity_for(style, is_minor)
            if copied.get("type") == "scatter3d":
                marker = dict(copied.get("marker") or {})
                marker["opacity"] = opacity
                copied["marker"] = marker
            else:
                copied["opacity"] = opacity
        elif role == "bond":
            if str(group_id) in bond_group_opacity:
                pseudo_bond = {"is_minor": is_minor, "_render_opacity_scale": bond_group_opacity[str(group_id)]}
                copied["opacity"] = bond_effective_opacity(pseudo_bond, style)
            else:
                # Preserve the trace's existing opacity (set by
                # _bond_mesh_traces using bond_effective_opacity with occ).
                if copied.get("opacity") is None:
                    pseudo_bond = {"is_minor": is_minor}
                    if "mv_opacity_scale" in meta:
                        pseudo_bond["_render_opacity_scale"] = meta.get("mv_opacity_scale")
                    copied["opacity"] = bond_effective_opacity(pseudo_bond, style)
        out.append(copied)
    return out


def _style_bool(style: dict, key: str, default: bool = False) -> bool:
    return bool(style.get(key, default))


def style_from_controls(
    atom_scale,
    bond_radius,
    minor_opacity,
    axis_scale,
    options,
    *,
    material: str | None = None,
    render_style: str | None = None,
    disorder: str | None = None,
    ortep_mode: str | None = None,
) -> dict:
    options = set(options or [])
    resolved_material = material or ("flat" if "fast_rendering" in options else "mesh")
    resolved_disorder = disorder or ("outline_rings" if "minor_wireframe" in options else "opacity")
    style = {
        "atom_scale": float(atom_scale),
        "bond_radius": float(bond_radius),
        "material": resolved_material,
        "style": render_style or "ball_stick",
        "disorder": resolved_disorder,
        "minor_opacity": float(minor_opacity),
        "axis_scale": float(axis_scale),
        "show_labels": "labels" in options,
        "show_axes": "axes" in options,
        "show_minor_only": "minor_only" in options,
        "minor_wireframe": "minor_wireframe" in options,
        "show_hydrogen": "hydrogens" in options,
        "show_unit_cell": "unit_cell_box" in options,
        "fast_rendering": "fast_rendering" in options,
        "topology_enabled": "topology" in options,
        "monochrome": "monochrome" in options,
    }
    if ortep_mode is not None:
        style["ortep_mode"] = ortep_mode
    return validate_style_schema(style)



__all__ = [name for name in globals() if not name.startswith("__")]
