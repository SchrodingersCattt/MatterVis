from __future__ import annotations

from typing import Any, Mapping


def atom_is_minor(atom: Mapping[str, Any]) -> bool:
    """Return the loader-authored minor-disorder flag for an atom.

    The renderer must not infer minor disorder from CIF PART strings or
    occupancy values. Ordered special-position atoms can look identical to
    disorder in those raw fields; only the loader's ordered-replica resolver is
    allowed to write ``_is_minor``.
    """
    return bool(atom.get("_is_minor", False))


def bond_is_minor(atom_i: Mapping[str, Any], atom_j: Mapping[str, Any]) -> bool:
    """A bond is minor when either rendered endpoint is a loader minor."""
    return atom_is_minor(atom_i) or atom_is_minor(atom_j)


def minor_opacity_for(style: Mapping[str, Any], is_minor: bool) -> float:
    """Resolve the base opacity for a major/minor render group."""
    if not is_minor:
        return float(style.get("major_opacity", 1.0))
    fade = style.get("disorder") == "opacity" or bool(style.get("force_minor_fade", False))
    if fade:
        return max(0.05, float(style.get("minor_opacity", 0.35)))
    return 1.0


def bond_effective_opacity(bond: Mapping[str, Any], style: Mapping[str, Any]) -> float:
    """Resolve final bond opacity after disorder and bond-group styling."""
    scale = bond.get("_render_opacity_scale", 1.0)
    try:
        scale_f = max(0.0, min(1.0, float(scale)))
    except (TypeError, ValueError):
        scale_f = 1.0
    return minor_opacity_for(style, bool(bond.get("is_minor", False))) * scale_f
