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
    assert "outline: 2px solid var(--mv-accent)" in css
