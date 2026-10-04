from __future__ import annotations

import pytest

from mat_viewer.config import atom_radius, covalent_radius, element_color, reload_config
from mat_viewer.utils.colors import ansi256_from_hex, element_ansi_color
from mat_viewer.config.colors import (
    ATOM_RADIUS,
    COVALENT_RADIUS,
    CUBE_ATOM_DISPLAY_RADII_ANG,
    CUBE_COVALENT_RADII_ANG,
    CUBE_ELEMENT_COLORS,
    CUBE_ELEMENT_SYMBOLS,
    ELEMENT_COLORS,
    ELEMENT_COLORS_LIGHT,
)


def test_periodic_element_tables_cover_ase_symbols():
    assert len(ELEMENT_COLORS) >= 119
    assert len(ELEMENT_COLORS_LIGHT) >= 119
    assert len(ATOM_RADIUS) >= 119
    assert len(COVALENT_RADIUS) >= 118
    assert len(CUBE_ELEMENT_SYMBOLS) >= 118
    assert len(CUBE_ELEMENT_COLORS) >= 119
    assert len(CUBE_COVALENT_RADII_ANG) >= 118
    assert len(CUBE_ATOM_DISPLAY_RADII_ANG) >= 119

    assert ELEMENT_COLORS["Ag"] != ELEMENT_COLORS["default"]
    assert COVALENT_RADIUS["Ag"] > 1.0
    assert ATOM_RADIUS["Ag"] > ATOM_RADIUS["default"]
    assert CUBE_ELEMENT_SYMBOLS[47] == "Ag"
    assert CUBE_ELEMENT_SYMBOLS[118] == "Og"
    assert min(value for key, value in ATOM_RADIUS.items() if key != "default") >= 0.15


def test_mattervis_hand_tuned_element_overrides_are_preserved():
    assert ELEMENT_COLORS["C"] == "#5E5E5E"
    assert ELEMENT_COLORS["H"] == "#DDDDDD"
    assert ELEMENT_COLORS["N"] == "#2C61AF"
    assert ELEMENT_COLORS["O"] == "#B85060"
    assert ELEMENT_COLORS["Cl"] == "#218E6A"
    assert ELEMENT_COLORS["Cu"] == "#B87333"
    assert ELEMENT_COLORS["Fe"] == "#B7410E"
    assert ELEMENT_COLORS["Ni"] == "#4C8C4A"
    assert ELEMENT_COLORS["Co"] == "#3F5FBF"
    assert ELEMENT_COLORS["Zn"] == "#7D80B8"

    assert ELEMENT_COLORS_LIGHT["C"] == "#888888"
    assert ELEMENT_COLORS_LIGHT["H"] == "#D8D8D8"
    assert ELEMENT_COLORS_LIGHT["N"] == "#8FADD4"
    assert ELEMENT_COLORS_LIGHT["O"] == "#D48A88"
    assert ELEMENT_COLORS_LIGHT["Cl"] == "#7DB88A"
    assert ELEMENT_COLORS_LIGHT["Cu"] == "#D19A66"
    assert ELEMENT_COLORS_LIGHT["Fe"] == "#D07A55"
    assert ELEMENT_COLORS_LIGHT["Ni"] == "#82B57F"
    assert ELEMENT_COLORS_LIGHT["Co"] == "#7F93D1"
    assert ELEMENT_COLORS_LIGHT["Zn"] == "#A6A8D0"

    assert ATOM_RADIUS["C"] == 0.18
    assert ATOM_RADIUS["H"] == 0.16
    assert ATOM_RADIUS["Cl"] == 0.24
    assert ATOM_RADIUS["Fe"] == 0.22

    assert COVALENT_RADIUS["C"] == 0.77
    assert COVALENT_RADIUS["H"] == 0.31
    assert COVALENT_RADIUS["Cl"] == 0.99
    assert COVALENT_RADIUS["Fe"] == 1.24


def test_cube_hand_tuned_overrides_are_preserved():
    assert CUBE_ELEMENT_COLORS["C"] == "#909090"
    assert CUBE_ELEMENT_COLORS["N"] == "#3050F8"
    assert CUBE_ELEMENT_COLORS["O"] == "#FF0D0D"
    assert CUBE_ELEMENT_COLORS["F"] == "#90E050"
    assert CUBE_ELEMENT_COLORS["Cl"] == "#1FF01F"
    assert CUBE_ELEMENT_COLORS["I"] == "#940094"

    assert CUBE_COVALENT_RADII_ANG["C"] == 0.76
    assert CUBE_COVALENT_RADII_ANG["O"] == 0.66
    assert CUBE_COVALENT_RADII_ANG["I"] == 1.39

    assert CUBE_ATOM_DISPLAY_RADII_ANG["H"] == 0.30
    assert CUBE_ATOM_DISPLAY_RADII_ANG["C"] == 0.55
    assert CUBE_ATOM_DISPLAY_RADII_ANG["I"] == 0.95


def test_charged_element_symbols_normalize_before_lookup():
    assert element_color("Fe2+") == element_color("Fe")
    assert element_color("  Cu1+", light=True) == element_color("Cu", light=True)
    assert atom_radius("Ni3+") == atom_radius("Ni")
    assert covalent_radius("Mn3+") == covalent_radius("Mn")


def test_canonical_palette_is_default_and_cpk_is_explicit():
    assert element_color("C") == "#5E5E5E"
    assert element_color("C", theme="cpk") == "#909090"
    assert element_color("O", theme="cpk") == "#FF0D0D"
    with pytest.raises(ValueError, match="light variant"):
        element_color("C", theme="cpk", light=True)
    with pytest.raises(ValueError, match="theme must be"):
        element_color("C", theme="unknown")


def test_tui_ansi_adapter_is_deterministic_and_uses_canonical_rgb():
    assert ansi256_from_hex("#5E5E5E") == ansi256_from_hex("#5E5E5E")
    assert element_ansi_color("C") == ansi256_from_hex("#5E5E5E")
    assert element_ansi_color("O") == ansi256_from_hex(element_color("O"))


def test_renderer_palette_contract_has_no_private_semantic_tables():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert "_ELEMENT_COLORS =" not in (root / "mat_viewer" / "render" / "planning.py").read_text(encoding="utf-8")
    assert "ELEMENT_COLORS: dict" not in (root / "mat_viewer" / "tui" / "renderer.py").read_text(encoding="utf-8")


def test_configured_canonical_override_reaches_terminal_adapter():
    reload_config(overrides={"colors": {"elements": {"O": "#010203"}}})
    try:
        assert element_color("O") == "#010203"
        assert element_ansi_color("O") == ansi256_from_hex("#010203")
    finally:
        reload_config("__missing_config__.toml")
