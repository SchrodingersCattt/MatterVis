"""Per-bond render-rule application (Phase 4: bond_groups model).

The Dash UI and REST API both let the caller pin a list of
*bond-group* rules onto a scene's state. Each rule has a *selector*
(``{"all": True}`` or ``{"between_elements": ["O","H"]}``) and zero
or more *overrides*: ``color``, ``visible``, ``opacity`` (a multiplier
in [0, 1]), and ``radius_scale`` (a multiplier on the per-scene
``bond_radius`` slider).

Rules apply in *list order*; later rows win on overlapping bonds. So
``[{all -> grey}, {between_elements: ["O","H"] -> red}]`` paints
every bond grey except the O-H bonds, which come out red.

This module is the single source of truth for that semantics. It
operates on the public scene dict shape: a ``bonds`` list whose
entries carry at minimum ``i``, ``j``, ``color_i``, ``color_j``,
``alpha_i``, ``alpha_j``, ``is_minor``, ``start``, ``end``, plus a
parallel ``draw_atoms`` list whose entries carry ``elem``.

Three public helpers:

* :func:`bond_matches_selector` -- one bond against one selector.
* :func:`tag_bonds_with_groups` -- decorate every bond dict with
  per-bond override fields (``_render_color``, ``_render_visible``,
  ``_render_opacity_scale``, ``_render_radius_scale``); the original
  bond dicts are never mutated.
* :func:`bond_groups_cache_key` -- deterministic hash of a bond_groups
  list, used by the renderer's figure-JSON cache.

The renderer (:mod:`crystal_viewer.renderer`) consumes these tags via
``_bond_segments`` after :func:`tag_bonds_with_groups` writes the
overrides; bonds whose ``_render_visible=False`` are skipped, and
``_render_radius_scale`` multiplies the global ``style["bond_radius"]``
inside the cylinder builder.

Phase 4 selector grammar (deliberately minimal -- we pick the two
most-asked-for filters and add more later as concrete UX needs come
up; the at-rest selector dict is a forward-compatible shape):

* ``{"all": True}`` -- every bond in the scene.
* ``{"between_elements": ["O", "H"]}`` -- match a bond when the
  *unordered* element pair of its two endpoints is the unordered
  pair of the selector list. Single-element lists (``["O"]``) match
  homo-element bonds (O-O); two distinct elements match either
  ordering (O-H or H-O). Element symbols are case-sensitive on
  whatever the loader put in ``draw_atoms[i]["elem"]``.

The grammar is intentionally a strict subset of the atom_groups
selector schema so a future "by-bond-index" or "by-fragment" filter
can land additively under :mod:`crystal_viewer.bond_groups` without
touching atom_groups.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _endpoint_elements(
    bond: Dict[str, Any],
    atoms: Sequence[Dict[str, Any]],
) -> Tuple[str, str]:
    """Return ``(elem_i, elem_j)`` for the bond's two endpoint atoms.

    ``atoms`` is the scene's ``draw_atoms`` list (or whatever list the
    bond's ``i`` / ``j`` integer indices reference). When ``i`` or
    ``j`` is out of range we return empty strings so the selector
    matcher simply rejects the bond rather than raising.
    """
    n = len(atoms)
    i = int(bond.get("i", -1))
    j = int(bond.get("j", -1))
    elem_i = str(atoms[i].get("elem")) if 0 <= i < n else ""
    elem_j = str(atoms[j].get("elem")) if 0 <= j < n else ""
    return elem_i, elem_j


def _bond_label_pair(bond: Dict[str, Any], atoms: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    """Return the ``(label_i, label_j)`` pair for a bond's endpoints."""
    n = len(atoms)
    i = int(bond.get("i", -1))
    j = int(bond.get("j", -1))
    label_i = str(atoms[i].get("label") or "") if 0 <= i < n else ""
    label_j = str(atoms[j].get("label") or "") if 0 <= j < n else ""
    return label_i, label_j


def bond_matches_selector(
    bond: Dict[str, Any],
    selector: Dict[str, Any],
    *,
    atoms: Sequence[Dict[str, Any]],
) -> bool:
    """Return True iff ``bond`` matches every key in ``selector``.

    The legal keys (intersected, AND semantics) are:

    * ``all``: matches every bond regardless of element.
    * ``between_elements``: list of element symbols. The selector
      matches when the unordered element pair of the bond's two
      endpoints equals the unordered pair (or self-pair, for
      single-element lists) in the selector. Length 3+ uses a
      "both endpoints in the listed set" rule (useful for "M-X
      where X is any halide").
    * ``labels``: list of bond identifiers, each formed by joining
      the two endpoint atom labels with ``"-"`` in either order
      (i.e. ``"Pb1-Cl3"`` or ``"Cl3-Pb1"`` both match a bond from
      ``Pb1`` to ``Cl3``). Used by per-instance overrides applied
      from the right-click bond menu.
    * ``is_minor``: matches the bond's ``is_minor`` flag exactly.
    """
    if selector.get("all"):
        return True
    matched_any = False
    between = selector.get("between_elements")
    if isinstance(between, (list, tuple)) and between:
        wanted = [str(item) for item in between if item is not None]
        elem_i, elem_j = _endpoint_elements(bond, atoms)
        if not elem_i or not elem_j:
            return False
        observed = sorted([elem_i, elem_j])
        if len(wanted) == 1:
            if not (observed[0] == wanted[0] and observed[1] == wanted[0]):
                return False
        elif len(wanted) == 2:
            if observed != sorted(wanted):
                return False
        else:
            wanted_set = set(wanted)
            if not (observed[0] in wanted_set and observed[1] in wanted_set):
                return False
        matched_any = True
    labels = selector.get("labels")
    if isinstance(labels, (list, tuple)) and labels:
        wanted_labels = {str(item) for item in labels if item is not None}
        label_i, label_j = _bond_label_pair(bond, atoms)
        forward = f"{label_i}-{label_j}"
        backward = f"{label_j}-{label_i}"
        if forward not in wanted_labels and backward not in wanted_labels:
            return False
        matched_any = True
    if "is_minor" in selector:
        if bool(bond.get("is_minor", False)) != bool(selector["is_minor"]):
            return False
        matched_any = True
    return matched_any


def tag_bonds_with_groups(
    bonds: Sequence[Dict[str, Any]],
    bond_groups: Sequence[Dict[str, Any]],
    *,
    atoms: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Decorate every bond dict with per-render override fields.

    Returns a new list of shallow-copied bond dicts. Each gains the
    following sentinels:

    * ``_render_color``: hex string -- the bond's effective override
      colour, or ``None`` to keep the per-atom-half default.
    * ``_render_visible``: bool. ``False`` means hide the bond.
    * ``_render_opacity_scale``: float in [0, 1]. Multiplies the
      bond's effective opacity (after disorder fade).
    * ``_render_opacity_group_id``: id of the last matching rule that
      supplied opacity, so opacity edits can restyle cached geometry.
    * ``_render_radius_scale``: float -- multiplies the scene-level
      ``style["bond_radius"]`` for this bond.

    Rules apply in list order; later matching rules win on each
    field independently.
    """
    tagged: List[Dict[str, Any]] = []
    for bond in bonds:
        decorated = dict(bond)
        decorated["_render_color"] = None
        decorated["_render_visible"] = True
        decorated["_render_opacity_scale"] = 1.0
        decorated["_render_opacity_group_id"] = None
        decorated["_render_radius_scale"] = 1.0
        for group in bond_groups:
            selector = group.get("selector") or {}
            if not bond_matches_selector(decorated, selector, atoms=atoms):
                continue
            color = group.get("color")
            if color:
                decorated["_render_color"] = color
            if "visible" in group:
                decorated["_render_visible"] = bool(group["visible"])
            opacity = group.get("opacity")
            if opacity is not None:
                decorated["_render_opacity_scale"] = max(0.0, min(1.0, float(opacity)))
                decorated["_render_opacity_group_id"] = str(group.get("id") or "")
            radius_scale = group.get("radius_scale")
            if radius_scale is not None:
                decorated["_render_radius_scale"] = max(0.0, float(radius_scale))
        tagged.append(decorated)
    return tagged


def bond_groups_cache_key(bond_groups: Optional[Sequence[Dict[str, Any]]]) -> Tuple:
    """Hashable summary of bond_groups for the figure-JSON cache.

    Two bond_groups lists hash to the same key iff they would produce
    the same render-time decoration. ``id`` and ``name`` are excluded
    so a row rename is a free operation; selector and override fields
    are included.
    """
    if not bond_groups:
        return ()
    parts: List[Tuple] = []
    for group in bond_groups:
        selector = group.get("selector") or {}
        parts.append(
            (
                bool(selector.get("all", False)),
                tuple(sorted(str(e) for e in selector.get("between_elements", []) or [])),
                str(group.get("color") or ""),
                bool(group.get("visible", True)),
                float(group.get("radius_scale")) if group.get("radius_scale") is not None else None,
            )
        )
    return tuple(parts)
