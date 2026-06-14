"""Scene-style helpers.

Moved from ``scene/core.py`` per the layered design in AGENTS.md.
These operate on scene dicts without importing the full rendering
pipeline.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional


def _resolve_element_color(elem: str, base: str, overrides: Dict[str, str]) -> str:
    if not overrides:
        return base
    override = overrides.get(elem)
    return override if override else base


def scene_style(
    scene: Dict[str, Any],
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge the scene's own style with an optional override dict."""
    from ..presets import DEFAULT_STYLE

    style = copy.deepcopy(DEFAULT_STYLE)
    style.update(scene.get("style", {}))
    if override:
        style.update(override)
    return style


def rebuild_scene_with_style(
    scene: Dict[str, Any],
    style: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a shallow copy of ``scene`` with ``style`` replaced."""
    updated = dict(scene)
    updated["style"] = scene_style(scene, style)
    return updated


def apply_element_colors(
    scene: Dict[str, Any],
    element_colors: Optional[Dict[str, str]] = None,
    element_colors_light: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Apply per-element hex-colour overrides to every atom and bond.

    Mutates ``scene`` in place and returns the same object for
    chaining. See ``agents/scene_api.md`` for the full contract.
    """
    if scene.get("style", {}).get("monochrome"):
        element_colors = {atom.get("elem", ""): "#000000" for atom in scene.get("draw_atoms", [])}
        element_colors_light = dict(element_colors)
    if not element_colors and not element_colors_light:
        return scene
    ec = element_colors or {}
    ec_light = element_colors_light or {}
    by_index: dict[int, tuple[str, str]] = {}
    for idx, atom in enumerate(scene.get("draw_atoms", [])):
        elem = atom.get("elem", "")
        new_color = _resolve_element_color(elem, atom.get("color", ""), ec)
        new_light = _resolve_element_color(elem, atom.get("color_light", ""), ec_light or ec)
        atom["color"] = new_color
        atom["color_light"] = new_light
        by_index[idx] = (new_color, new_light)
    for bond in scene.get("bonds", []):
        ci = by_index.get(int(bond.get("i", -1)))
        cj = by_index.get(int(bond.get("j", -1)))
        if ci is not None:
            bond["color_i"] = ci[0]
        if cj is not None:
            bond["color_j"] = cj[0]
    return scene


def merge_structure_style(
    preset: Dict[str, Any],
    name: str,
    style: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge a structure-level style override into a preset copy."""
    from ..presets import deep_merge, default_preset, json_safe

    merged = default_preset() if preset is None else copy.deepcopy(preset)
    merged["style"] = deep_merge(merged.get("style", {}), style)
    merged.setdefault("structures", {})
    merged["structures"].setdefault(name, {})
    merged["structures"][name]["style"] = json_safe(style)
    return merged
