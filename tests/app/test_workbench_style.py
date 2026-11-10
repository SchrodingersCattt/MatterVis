"""Keep the native workbench hierarchy and visual tokens stable."""

from pathlib import Path


ASSETS = Path(__file__).parents[2] / "frontend" / "assets"


def test_workbench_styles_define_scene_hierarchy_and_focus_tokens() -> None:
    css = (ASSETS / "workbench.css").read_text(encoding="utf-8")

    for selector in (
        ".workbench-brand",
        ".scene-tabs-card",
        ".scene-rename-row",
        ".structure-summary-card",
        ".upload-dropzone",
    ):
        assert selector in css
    assert "--mv-accent: #0f766e" in css
    assert "--mv-scene-tab-max-width: 220px" in css
    assert "overflow-x: auto" in css
    assert "overscroll-behavior-inline: contain" in css
    assert "scroll-padding-inline: 28px" in css
    assert "flex-direction: row !important" in css
    assert "flex: 0 0 auto !important" in css
    assert "min-width: 4.5rem !important" in css
    assert ".scene-header" in css
    assert "flex-wrap: wrap" in css
    header = css.split(".scene-header {", 1)[1].split("}", 1)[0]
    actions = css.split(".scene-actions {", 1)[1].split("}", 1)[0]
    rename = css.split(".scene-rename-row {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: wrap" in header
    assert "flex-wrap: wrap" in actions
    assert "flex-wrap: wrap" in rename
    assert "width: auto !important" in css
    assert "minmax(0, 1fr) auto" in css
    assert "outline: 2px solid var(--mv-accent)" in css
    assert "color-scheme: light" in css
    assert "color-scheme: dark" in css
    dark_block = css.split("@media (prefers-color-scheme: dark)", 1)[1][:1200]
    assert "--mv-page:" in dark_block
    assert "color-scheme: dark" in dark_block
    appearance = (ASSETS / "selection_appearance.css").read_text(encoding="utf-8")
    script = (ASSETS / "selection_appearance.js").read_text(encoding="utf-8")
    dock = appearance.split("#mv-selection-appearance {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in dock
    assert "position:absolute" not in dock
    assert "paddingRight" in script
    assert "document.body" in script
