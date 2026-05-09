"""Per-atom render-rule application for the Phase 2 atom_groups model.

The Dash UI and REST API both let the caller pin a list of atom-group
rules onto a scene's state. Each rule has a *selector*
(``{"all": True}``, ``{"elements": ["O","S"]}``, or
``{"is_minor": True/False}``) and zero or more *overrides*: ``color``,
``color_light``, ``visible``, ``opacity`` (a multiplier 0-1),
``material`` (``"mesh"``/``"flat"``), and ``style``
(``"ball"``/``"ball_stick"``/``"stick"``/``"ortep"``/``"wireframe"``).

Rules apply in *list order*; later rows win on overlapping atoms. So
``[{all -> grey}, {elements: O -> red}]`` paints everything grey
except oxygens, which come out red.

This module is the single source of truth for that semantics. It
operates on the public scene dict shape: a ``draw_atoms`` list whose
entries carry at minimum ``elem``, ``color``, ``color_light``,
``cart``, ``atom_radius``, ``is_minor``, ``label``. Everything else
on the atom dict is preserved.

Two public helpers:

- :func:`atoms_match_selector` checks one atom against one selector;
  the renderer uses this in tight loops for bond filtering.
- :func:`tag_atoms_with_groups` returns a NEW list of atom dicts with
  per-atom override fields (``_render_color``, ``_render_visible``,
  ``_render_opacity_scale``, ``_render_material``, ``_render_style``)
  decorated. The original atom dicts are never mutated, so caching
  layers that hash the scene by id stay correct.

The renderer (:mod:`crystal_viewer.renderer`) consumes these tags via
:func:`partition_draw_atoms_by_render_pipeline` to dispatch each
(material, style) subset to its existing trace builder.
"""
from __future__ import annotations

from typing import Any


def atom_matches_selector(atom: dict, selector: dict) -> bool:
    """Return True iff ``atom`` matches every key in ``selector``.

    The legal keys (intersected, AND semantics) are:
    - ``all``: matches every atom regardless of element / disorder.
    - ``elements``: list of element symbols; matches when
      ``atom['elem']`` is in the list.
    - ``is_minor``: matches the atom's ``is_minor`` flag exactly.
    """
    if selector.get("all"):
        return True
    if "elements" in selector:
        if atom.get("elem") not in selector["elements"]:
            return False
    if "is_minor" in selector:
        if bool(atom.get("is_minor", False)) != bool(selector["is_minor"]):
            return False
    # If the selector reached here without any of {all, elements, is_minor}
    # being present, treat it as a no-op (don't match anything). The
    # backend normaliser should reject empty selectors before they reach
    # this layer, but we belt-and-brace the renderer too.
    if not (
        selector.get("all")
        or "elements" in selector
        or "is_minor" in selector
    ):
        return False
    return True


def tag_atoms_with_groups(
    atoms: list[dict[str, Any]],
    atom_groups: list[dict[str, Any]],
    *,
    scene_material: str | None = None,
    scene_style: str | None = None,
) -> list[dict[str, Any]]:
    """Decorate every atom dict with per-render override fields.

    Returns a new list of shallow-copied atom dicts. Each gains the
    following fields:

    - ``_render_color``: hex string -- the atom's effective major-side
      colour after group rules. Defaults to ``atom['color']``.
    - ``_render_color_light``: same idea for the minor-side palette;
      defaults to ``atom['color_light']`` (or ``_render_color`` when
      the atom has no light variant).
    - ``_render_visible``: bool. ``False`` means hide the atom and any
      bond touching it.
    - ``_render_opacity_scale``: float in [0, 1]. Multiplies the
      atom's effective opacity (after disorder fade is applied).
    - ``_render_material``: ``"mesh" | "flat" | None``. Per-atom
      override of ``style['material']``. ``None`` = inherit.
    - ``_render_style``: ``"ball"|"ball_stick"|"stick"|"ortep"|"wireframe"|None``.

    ``scene_material`` / ``scene_style`` are not used to populate the
    override (we keep ``None`` for "inherit"); they're available for
    future bond colouring extensions.
    """
    tagged: list[dict[str, Any]] = []
    for atom in atoms:
        decorated = dict(atom)
        # Sentinels: ``None`` means "no group provided an override; the
        # builder should fall back to the element-palette colour /
        # ``_style_color`` so the legacy monochrome flag still wins".
        # Whenever a matching group supplies an override, we replace
        # the sentinel with the explicit value.
        decorated["_render_color"] = None
        decorated["_render_color_light"] = None
        decorated["_render_visible"] = True
        decorated["_render_opacity_scale"] = 1.0
        decorated["_render_material"] = None
        decorated["_render_style"] = None
        for group in atom_groups:
            selector = group.get("selector") or {}
            if not atom_matches_selector(decorated, selector):
                continue
            color = group.get("color")
            if color:
                decorated["_render_color"] = color
                # If the user provided a single colour, default the
                # light variant to the same hue so disorder-fade tints
                # don't drift back toward the element palette.
                if not group.get("color_light"):
                    decorated["_render_color_light"] = color
            color_light = group.get("color_light")
            if color_light:
                decorated["_render_color_light"] = color_light
            if "visible" in group:
                decorated["_render_visible"] = bool(group["visible"])
            opacity = group.get("opacity")
            if opacity is not None:
                # Replace (not multiply) so the last matching group's
                # opacity is the visible result. Layered ``[all -> 50%,
                # O -> 30%]`` => O ends up at 30%; everything else at
                # 50%; nothing drifts to zero from accidental stacking.
                decorated["_render_opacity_scale"] = max(0.0, min(1.0, float(opacity)))
            material = group.get("material")
            if material:
                decorated["_render_material"] = material
            sty = group.get("style")
            if sty:
                decorated["_render_style"] = sty
        tagged.append(decorated)
    return tagged


def partition_atoms_by_render_pipeline(
    tagged_atoms: list[dict[str, Any]],
    *,
    scene_material: str,
    scene_style: str,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Split tagged atoms into one bucket per (effective_material,
    effective_style) tuple. Hidden atoms are dropped entirely.

    The renderer then runs the appropriate sub-trace builder on each
    bucket using a sub-scene with that bucket's atoms. Default bucket
    (atoms without per-group material/style overrides) is keyed on
    ``(scene_material, scene_style)``; everything else gets its own
    bucket so per-group ORTEP / wireframe / flat overrides actually
    take effect."""
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for atom in tagged_atoms:
        if not atom.get("_render_visible", True):
            continue
        material = atom.get("_render_material") or scene_material
        style = atom.get("_render_style") or scene_style
        buckets.setdefault((material, style), []).append(atom)
    return buckets


def hidden_atom_label_set(tagged_atoms: list[dict[str, Any]]) -> set[str]:
    """Labels of atoms suppressed by atom_group ``visible: false``.
    The renderer uses this to also drop bonds and labels touching
    hidden atoms (a half-bond going to nowhere reads as a bug).
    """
    return {
        str(atom.get("label"))
        for atom in tagged_atoms
        if not atom.get("_render_visible", True) and atom.get("label") is not None
    }
