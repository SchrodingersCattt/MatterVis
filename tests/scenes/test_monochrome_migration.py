"""Lock in the legacy ``monochrome`` flag auto-migration into
``atom_groups``.

Background
----------
Phase 2 added the ``atom_groups`` rule list as the per-scene single
source of truth for atom-style overrides. The legacy ``monochrome``
checkbox / display option had to keep working for old presets and
agent scripts, but having BOTH the flag and an atom_groups rule
active would double-apply: the user adds a "red O" rule on top of a
monochrome scene and gets "all-black except red O" with an
unexpected extra "all-black" rule baked in.

Fix
---
``ViewerBackend.normalize_state`` promotes a ``monochrome``
display-option into a single ``{"selector": {"all": True}, "color":
"#000000"}`` atom_group rule the first time it sees that patch.
After that the renderer ignores ``style["monochrome"]`` whenever
``atom_groups`` is non-empty (see ``_style_color`` in
``crystal_viewer.renderer``).

These tests pin both halves of that contract.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from crystal_viewer.app import WORKSPACE_DIR, ViewerBackend


@pytest.fixture
def backend(tmp_path: Path):
    preset = str(tmp_path / "preset.json")
    return ViewerBackend(preset_path=preset, root_dir=WORKSPACE_DIR)


def test_monochrome_display_option_promotes_to_atom_group(backend: ViewerBackend):
    state = backend.patch_state({"display_options": ["labels", "monochrome"]})
    groups = state.get("atom_groups") or []
    assert any(
        (g.get("selector") or {}).get("all") and g.get("color") == "#000000"
        for g in groups
    ), (
        "monochrome display-option must be auto-promoted to a single "
        "{all -> #000000} atom_group rule on first patch_state call. "
        f"got groups={groups}"
    )


def test_monochrome_migration_is_idempotent(backend: ViewerBackend):
    backend.patch_state({"display_options": ["monochrome"]})
    first = backend.get_state().get("atom_groups") or []
    backend.patch_state({"display_options": ["monochrome"]})
    second = backend.get_state().get("atom_groups") or []
    assert len(first) == len(second), (
        "re-posting monochrome must not stack a second migrated group"
    )


def test_explicit_color_rule_blocks_migration(backend: ViewerBackend):
    """If the user already added an explicit colour rule (eg. red O),
    a follow-up monochrome toggle must NOT silently inject an
    all-black rule on top -- that would produce the
    "all-black-except-red-O" the user did not ask for."""
    backend.add_atom_group(selector={"elements": ["O"]}, color="#FF0000")
    backend.patch_state({"display_options": ["monochrome"]})
    groups = backend.get_state().get("atom_groups") or []
    has_mono = any(
        (g.get("selector") or {}).get("all") and g.get("color") == "#000000"
        for g in groups
    )
    assert not has_mono, (
        "explicit colour rule must inhibit monochrome auto-promotion "
        "(otherwise the user gets a surprise 'all-black' overlay)."
    )


def test_renderer_ignores_monochrome_when_atom_groups_present():
    """Renderer-level: with atom_groups set, ``_style_color`` is a
    no-op even if ``style['monochrome']=True`` (the migration above
    has already turned monochrome into an atom_groups rule, so this
    just guards against double-apply)."""
    from crystal_viewer.renderer import _style_color

    style = {"monochrome": True, "atom_groups": [{"selector": {"all": True}, "color": "#000000"}]}
    assert _style_color("#FFAA00", style) == "#FFAA00", (
        "monochrome flag must be inert when atom_groups is non-empty"
    )

    bare_style = {"monochrome": True, "atom_groups": []}
    assert _style_color("#FFAA00", bare_style) == "#000000", (
        "without atom_groups the legacy monochrome flag still wins"
    )
