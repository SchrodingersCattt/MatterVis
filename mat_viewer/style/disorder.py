from __future__ import annotations

from typing import Any, Mapping


_MISSING = object()


def _field(record: Any, name: str, default: Any = None) -> Any:
    """Read one field from either a public mapping or a record object."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _has_field(record: Any, name: str) -> bool:
    """Return whether a public mapping or record object exposes ``name``."""
    if isinstance(record, Mapping):
        return name in record
    return hasattr(record, name)


def atom_is_minor(atom: Any) -> bool:
    """Return the loader-authored minor-disorder flag for an atom.

    The renderer must not infer minor disorder from CIF PART strings or
    occupancy values. Ordered special-position atoms can look identical to
    disorder in those raw fields; only the loader's ordered-replica resolver is
    allowed to write ``_is_minor``.
    """
    # Scene mappings use the loader's private provenance key.  CrystalIR's
    # public AtomIR carries the same provenance as its ``is_minor`` field.
    if _has_field(atom, "_is_minor"):
        return bool(_field(atom, "_is_minor"))
    if isinstance(atom, Mapping):
        return False
    return bool(_field(atom, "is_minor", False))


def atom_is_disordered(atom: Any) -> bool:
    """Return whether the loader identified an atom as part of disorder.

    The loader writes ``_is_minor=False`` on the chosen major alternative and
    ``_is_minor=True`` on the remaining alternatives. Key presence therefore
    distinguishes explicit disorder from an ordered partial-occupancy site;
    object records expose the same minor provenance through ``is_minor``.
    """
    if _has_field(atom, "is_disordered"):
        return bool(_field(atom, "is_disordered"))
    if _has_field(atom, "_is_minor"):
        return True
    return bool(_field(atom, "is_minor", False))


def bond_is_minor(atom_i: Any, atom_j: Any) -> bool:
    """A bond is minor when either rendered endpoint is a loader minor."""
    return atom_is_minor(atom_i) or atom_is_minor(atom_j)


def bond_is_disordered(atom_i: Any, atom_j: Any) -> bool:
    """A bond is disordered when either rendered endpoint is disordered."""
    return atom_is_disordered(atom_i) or atom_is_disordered(atom_j)


def minor_opacity_for(style: Mapping[str, Any], is_minor: bool) -> float:
    """Resolve the base opacity for a major/minor render group."""
    if not is_minor:
        return float(style.get("major_opacity", 1.0))
    fade = style.get("disorder") == "opacity" or bool(style.get("force_minor_fade", False))
    if fade:
        return max(0.05, float(style.get("minor_opacity", 0.35)))
    return 1.0


def bond_effective_opacity(
    bond: Any,
    style: Mapping[str, Any],
    *,
    atom_i: Any = None,
    atom_j: Any = None,
) -> float:
    """Resolve final bond opacity after disorder and bond-group styling.

    ``atom_i`` and ``atom_j`` let callers that receive public, minimally
    decorated bond records recover disorder provenance from their endpoints.
    Scene builders normally copy these fields onto the bond itself, but the
    endpoint fallback keeps CPU planning consistent for older/public inputs.
    """
    scale = _field(bond, "_render_opacity_scale", 1.0)
    try:
        scale_f = max(0.0, min(1.0, float(scale)))
    except (TypeError, ValueError):
        scale_f = 1.0
    if scale_f < 0.999 or _field(bond, "_render_opacity_group_id") is not None:
        return scale_f

    is_minor = bool(_field(bond, "is_minor", False))
    disordered_value = _field(bond, "is_disordered", _MISSING)
    if disordered_value is _MISSING:
        disordered_value = None
    if disordered_value is None and atom_i is not None and atom_j is not None:
        is_disordered = atom_is_disordered(atom_i) or atom_is_disordered(atom_j)
    else:
        is_disordered = bool(disordered_value if disordered_value is not None else is_minor)
    # Every loader-confirmed disorder component uses its crystallographic
    # occupancy as visual weight unless disorder rendering is disabled.
    if is_disordered and style.get("disorder") != "none":
        occ = _field(bond, "occ", _MISSING)
        if occ is _MISSING:
            occ = _field(bond, "occupancy", None)
        if occ is None and atom_i is not None and atom_j is not None:
            endpoint_occupancies = []
            for atom in (atom_i, atom_j):
                value = _field(atom, "occ", _MISSING)
                if value is _MISSING:
                    value = _field(atom, "occupancy", 1.0)
                try:
                    endpoint_occupancies.append(float(value))
                except (TypeError, ValueError):
                    endpoint_occupancies.append(1.0)
            occ = min(endpoint_occupancies)
        try:
            occ_f = float(occ)
        except (TypeError, ValueError):
            occ_f = 1.0
        return max(0.0, min(1.0, occ_f))
    return minor_opacity_for(style, is_minor)


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
    if atom_is_disordered(at):
        try:
            return max(0.0, min(1.0, float(at.get("occ", 1.0))))
        except (TypeError, ValueError):
            return 1.0
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
