"""Keep the narrow browser workbench usable without clipping sidebars."""

from pathlib import Path


ASSETS = Path(__file__).parents[2] / "frontend" / "assets"


def test_compact_layout_uses_overlay_toggles_and_breakpoint() -> None:
    script = (ASSETS / "panel_resize.js").read_text(encoding="utf-8")
    css = (ASSETS / "panel_resize.css").read_text(encoding="utf-8")

    assert "COMPACT_BREAKPOINT = 756" in script
    assert 'compact-toggle-" + panelId' in script
    assert "compact-panel-open" in script
    assert "@media (max-width: 755px)" in css
    assert "#viewer-root.compact-layout > #center-panel" in css
    assert "translateX(-105%)" in css
    assert "translateX(105%)" in css
    assert "compact-panel-open ~ .compact-panel-toggle--left" in css


def test_perf_log_collapses_to_a_narrow_mobile_safe_pill() -> None:
    css = (ASSETS / "panel_resize.css").read_text(encoding="utf-8")

    assert "width: 128px" in css
    assert "width: 118px" in css
    assert "max-width: calc(100% - 16px)" in css
    assert "@media (max-width: 755px)" in css


def test_panel_resize_repaints_plotly_after_flex_layout_settles() -> None:
    script = (ASSETS / "panel_resize.js").read_text(encoding="utf-8")

    assert "function resizeSceneNow()" in script
    assert "window.requestAnimationFrame" in script
    assert "resizeSceneNow();" in script
