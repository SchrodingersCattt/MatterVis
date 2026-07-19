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
    if scale_f < 0.999:
        return scale_f
    # In disorder="opacity" mode, use crystallographic occupancy.
    if style.get("disorder") == "opacity" or style.get("force_minor_fade"):
        occ = bond.get("occ", 1.0)
        try:
            occ_f = float(occ)
        except (TypeError, ValueError):
            occ_f = 1.0
        if occ_f < 0.999:
            return max(0.05, occ_f)
    return minor_opacity_for(style, bool(bond.get("is_minor", False)))


# ── Disorder helpers ────────────────────────────────────────────────────────
def _has_disorder_metadata(at):
    dg = at.get('dg', '').strip()
    da = at.get('da', '').strip()
    occ = float(at.get('occ', 1.0))
    return dg not in ('.', '?', '') or da not in ('.', '?', '') or occ < 0.999


def is_major(at):
    if '_is_major' in at:
        return bool(at['_is_major'])
    if not _has_disorder_metadata(at):
        return True
    return not is_minor(at)

def is_minor(at):
    # Loader provenance is the single source of truth for render fading.
    return atom_is_minor(at)

def disorder_alpha(at):
    if is_minor(at):
        return 0.22   # minor disorder: clearly faded behind major atoms
    return 1.0

def _disorder_group_id(at):
    """Return a canonical disorder group identifier for conflict checking."""
    synthetic_dg = str(at.get('_mv_auto_disorder_group') or '').strip()
    if synthetic_dg not in ('', '.', '?'):
        synthetic_da = str(at.get('_mv_auto_disorder_assembly') or 'mv_auto').strip()
        if synthetic_da in ('', '.', '?'):
            synthetic_da = 'mv_auto'
        return (synthetic_da, synthetic_dg)
    dg = at['dg'].strip()
    da = at['da'].strip()
    if dg in ('.', '?', ''):
        return None
    return (da, dg)


__all__ = [name for name in globals() if not name.startswith("__")]
