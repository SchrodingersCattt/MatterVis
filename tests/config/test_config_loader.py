from __future__ import annotations

import pytest

from crystal_viewer.config import current_config, reload_config
from crystal_viewer.config.loader import load_config


def test_builtin_config_is_read_only_and_exposes_defaults():
    cfg = load_config()

    assert cfg.style.get("atom_scale") == 1.0
    assert cfg.style.get("bond_radius") == 0.15
    assert cfg.colors.get("elements")["C"] == "#5E5E5E"
    with pytest.raises(TypeError):
        cfg.colors.values["selection_highlight"] = "#000000"  # type: ignore[index]


def test_toml_override_file_merges_known_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[style]
atom_scale = 1.25
unknown_style = "ignored"

[colors]
selection_highlight = "#ABCDEF"

[mck_overrides]
gap_threshold = 0.42
""",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.style.get("atom_scale") == 1.25
    assert "unknown_style" not in cfg.style.values
    assert cfg.colors.get("selection_highlight") == "#ABCDEF"
    assert cfg.mck_overrides.get("gap_threshold") == 0.42
    assert str(path) in cfg.source_paths


def test_reload_config_swaps_current_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[style]\natom_scale = 1.33\n", encoding="utf-8")

    try:
        reload_config(str(path))
        assert current_config().style.get("atom_scale") == 1.33
    finally:
        reload_config("__missing_config__.toml")
