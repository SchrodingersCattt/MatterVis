"""Representation identity for one drawn atom.

A drawing is the triple ``(source_site_id, image_shift, instance_kind)``.
Source-site counts are the unique canonical ids on instances whose kind is
``source``. Drawing counts are instances. Any other kind is a visual copy
of a source site, not an additional crystallographic site.

Replica producers still write ``_is_boundary_replica``,
``_is_bonded_image_replica``, and ``_is_framework_context_replica``. Those
private flags are assembly stamps. :func:`drawing_identity` reads them only
when ``instance_kind`` is absent, in that order: boundary, then bonded
image, then framework context, otherwise ``source``.

The flag fallback is not a second provenance standard. A framework-context
replica also carries the boundary stamp, so the fallback cannot tell those
two kinds apart. New replica producers must call
:func:`stamp_drawing_identity`. Remove the fallback once every scene rebuild
stamps ``instance_kind``; do not add another flag-only path.
``image_shift`` is the identity component. ``_image_shift`` is read only
when ``image_shift`` was not stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Integral, Real
from typing import Any

INSTANCE_SOURCE = "source"
INSTANCE_BOUNDARY = "boundary"
INSTANCE_BONDED_IMAGE = "bonded_image"
INSTANCE_FRAMEWORK_CONTEXT = "framework_context"

INSTANCE_KINDS = (
    INSTANCE_SOURCE,
    INSTANCE_BOUNDARY,
    INSTANCE_BONDED_IMAGE,
    INSTANCE_FRAMEWORK_CONTEXT,
)

_ZERO_SHIFT = (0, 0, 0)
_INDEX_KEYS = ("_source_index", "source_index", "site_index")
# Boundary before bonded-image before framework. A framework-context
# replica also carries the boundary stamp, so producers must set
# ``instance_kind`` themselves when the more specific kind matters.
_FLAG_KINDS = (
    ("_is_boundary_replica", INSTANCE_BOUNDARY),
    ("_is_bonded_image_replica", INSTANCE_BONDED_IMAGE),
    ("_is_framework_context_replica", INSTANCE_FRAMEWORK_CONTEXT),
)


def stamp_drawing_identity(
    atom: dict[str, Any],
    *,
    source_site_id: Any,
    image_shift: Any,
    instance_kind: str,
) -> dict[str, Any]:
    """Set ``(source_site_id, image_shift, instance_kind)`` on ``atom``.

    Existing private replica flags are left in place. ``source_site_id`` is
    stored as text. ``image_shift`` is stored as three integers.
    """
    if not isinstance(atom, dict):
        raise TypeError("drawing identity requires an atom dict")
    if instance_kind not in INSTANCE_KINDS:
        allowed = ", ".join(INSTANCE_KINDS)
        raise ValueError(f"instance_kind must be one of {allowed}")
    atom["source_site_id"] = "" if source_site_id is None else str(source_site_id)
    atom["image_shift"] = _image_shift_value(image_shift, strict=True)
    atom["instance_kind"] = instance_kind
    return atom


def drawing_identity(atom: Any) -> tuple[str, tuple[int, int, int], str]:
    """Return ``(source_site_id, image_shift, instance_kind)`` for ``atom``.

    An explicit ``instance_kind`` wins. Otherwise the kind is derived from
    the assembly stamps and the source id from ``_source_index``,
    ``source_index``, or ``site_index`` when one of those is present.
    ``image_shift`` is the stored field. ``_image_shift`` is used only when
    ``image_shift`` is absent, and a missing shift is ``(0, 0, 0)``.
    """
    if not isinstance(atom, dict):
        return ("", _ZERO_SHIFT, INSTANCE_SOURCE)
    shift = _stored_image_shift(atom)
    kind = atom.get("instance_kind")
    if kind:
        source_id = _explicit_source_site_id(atom)
        if not source_id:
            source_id = _index_site_id(atom)
        return (source_id, shift, str(kind))
    return (_index_site_id(atom), shift, _kind_from_flags(atom))


def atom_is_visual_copy(atom: Any) -> bool:
    """Return whether this drawing repeats a source site."""
    if not isinstance(atom, dict):
        return False
    _source_site_id, _image_shift, instance_kind = drawing_identity(atom)
    return instance_kind != INSTANCE_SOURCE


def indexed_source_site_id(atom: Any, fallback: Any = "") -> str:
    """Canonical site id already stored on ``atom``, else an index.

    ``source_site_id`` wins when set. Otherwise the first present value
    among ``_source_index``, ``source_index``, and ``site_index`` is used.
    ``fallback`` is the source index a replica producer already knows when
    the atom dict does not carry one.
    """
    if isinstance(atom, dict):
        explicit = _explicit_source_site_id(atom)
        if explicit:
            return explicit
        indexed = _index_site_id(atom)
        if indexed:
            return indexed
    return _text_site_id(fallback) or ""


def _explicit_source_site_id(atom: Mapping[str, Any]) -> str:
    if "source_site_id" not in atom:
        return ""
    return _text_site_id(atom.get("source_site_id")) or ""


def _index_site_id(atom: Mapping[str, Any]) -> str:
    for key in _INDEX_KEYS:
        if key not in atom:
            continue
        text = _text_site_id(atom.get(key))
        if text:
            return text
    return ""


def _text_site_id(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Integral):
        return str(int(value))
    if isinstance(value, Real):
        real = float(value)
        if not real.is_integer():
            return None
        return str(int(real))
    text = str(value).strip()
    return text or None


def stamp_home_source(atom: dict[str, Any], *, fallback: Any = "") -> dict[str, Any]:
    """Stamp one home-cell drawing as a ``source`` instance.

    Display-mode rebuilds call this for atoms that a replica producer did
    not already stamp. A copied replica kind is replaced only when the
    caller has already cleared ``instance_kind``.
    """
    shift = atom.get("_image_shift", atom.get("image_shift", _ZERO_SHIFT))
    return stamp_drawing_identity(
        atom,
        source_site_id=indexed_source_site_id(atom, fallback),
        image_shift=shift,
        instance_kind=INSTANCE_SOURCE,
    )


def ensure_source_identity(atoms: list[Any]) -> list[Any]:
    """Stamp source identity on drawings a replica producer left blank.

    A drawing that already carries a replica flag keeps that flag. Stamping
    it as ``source`` would hide the copy from the source-site count.
    """
    for index, atom in enumerate(atoms):
        if not isinstance(atom, dict) or atom.get("instance_kind"):
            continue
        if any(atom.get(flag) for flag, _kind in _FLAG_KINDS):
            continue
        stamp_home_source(atom, fallback=index)
    return atoms


def _stored_image_shift(atom: Mapping[str, Any]) -> tuple[int, int, int]:
    if "image_shift" in atom:
        return _image_shift_value(atom.get("image_shift"), strict=False)
    if "_image_shift" in atom:
        return _image_shift_value(atom.get("_image_shift"), strict=False)
    return _ZERO_SHIFT


def _kind_from_flags(atom: Mapping[str, Any]) -> str:
    for flag, kind in _FLAG_KINDS:
        if atom.get(flag):
            return kind
    return INSTANCE_SOURCE


def _image_shift_value(value: Any, *, strict: bool) -> tuple[int, int, int]:
    if isinstance(value, (str, bytes)) or value is None:
        if strict:
            raise ValueError("image_shift must be three integers")
        return _ZERO_SHIFT
    try:
        parts = tuple(value)
    except TypeError as exc:
        if strict:
            raise ValueError("image_shift must be three integers") from exc
        return _ZERO_SHIFT
    if len(parts) != 3:
        if strict:
            raise ValueError("image_shift must be three integers")
        return _ZERO_SHIFT
    converted: list[int] = []
    for part in parts:
        if isinstance(part, bool):
            if strict:
                raise ValueError("image_shift must be three integers")
            return _ZERO_SHIFT
        try:
            converted.append(int(part))
        except (TypeError, ValueError) as exc:
            if strict:
                raise ValueError("image_shift must be three integers") from exc
            return _ZERO_SHIFT
    return (converted[0], converted[1], converted[2])
