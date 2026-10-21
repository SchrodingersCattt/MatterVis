"""Layout regressions for scene tabs, dropdown labels, and zoom refit.

Items 5, 17, 19, 20, and 38 from the umbrella QA issue. The source checks
run without a browser. The Chrome check drives the real stylesheets against
Dash 4's tab and dropdown rules.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.app.test_camera_browser_regression import (
    _chrome,
    _playwright_module,
    _plotly_bundle,
    _unavailable,
)


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "frontend" / "assets"
RUNNER = Path(__file__).resolve().parent / "layout_responsive_browser.js"
LEFT_PANEL = ROOT / "mat_viewer" / "app" / "layout_left_panel.py"


def test_scene_tabs_scroll_instead_of_stacking() -> None:
    css = (ASSETS / "workbench.css").read_text(encoding="utf-8")
    layout = LEFT_PANEL.read_text(encoding="utf-8")

    assert "mobile_breakpoint" in layout
    assert "_SCENE_TAB_EXTRAS[\"mobile_breakpoint\"] = 0" in layout
    assert "#scene-tabs.tab-container" in css
    assert "flex-direction: row !important" in css
    assert "flex-wrap: nowrap !important" in css
    assert "overflow-x: auto" in css
    assert "height: auto !important" in css
    assert "scene-tab-scroll" in css
    assert "ensureTabScrollers" in (ASSETS / "panel_resize.js").read_text(encoding="utf-8")
    assert "--mv-scene-tab-min-width" not in css
    assert "flex: 0 0 var(--mv-scene-tab-min-width)" not in css


def test_dropdown_labels_keep_the_flexible_track() -> None:
    css = (ASSETS / "workbench.css").read_text(encoding="utf-8")

    assert "grid-template-columns: minmax(0, 1fr) auto;" in css
    assert ".dash-dropdown-value" in css
    assert ".mv-style-row > *" in css


def test_panel_width_tracks_the_layout_viewport() -> None:
    script = (ASSETS / "panel_resize.js").read_text(encoding="utf-8")
    layout = LEFT_PANEL.read_text(encoding="utf-8")

    assert '"left-panel": 0.26' in script
    assert "rememberFraction" in script
    assert "visualViewport" in script
    assert "maxWidth" not in layout.split("id=\"left-panel\"", 1)[1].split("style={", 1)[1].split("},", 1)[0]


def test_canvas_refit_uses_the_center_panel_box() -> None:
    script = (ASSETS / "panel_resize.js").read_text(encoding="utf-8")
    camera = (ASSETS / "view_camera.js").read_text(encoding="utf-8")

    assert "function canvasHostSize()" in script
    assert 'document.getElementById("center-panel")' in script
    assert "width: size.width" in script
    assert "height: size.height" in script
    assert "MatterVisPanelResize" in camera
    assert 'observer.observe(host)' in camera


@pytest.mark.browser
@pytest.mark.timeout(120)
def test_headless_zoom_and_narrow_layout(tmp_path: Path) -> None:
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    bundle = _plotly_bundle()
    if not node or not chrome or not playwright or bundle is None:
        _unavailable("layout browser gate needs node, Chrome/Chromium, Playwright, and plotly.min.js")
    shots = tmp_path / "shots"
    try:
        result = subprocess.run(
            [
                node,
                str(RUNNER),
                playwright,
                str(bundle),
                str(ASSETS / "workbench.css"),
                str(ASSETS / "panel_resize.css"),
                str(ASSETS / "panel_resize.js"),
                str(shots),
                chrome,
            ],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            cwd=str(ROOT),
            env={**os.environ, "NODE_PATH": str(ROOT / "node_modules")},
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail((exc.stderr or exc.stdout or "layout browser check timed out")[-4000:])
    if result.returncode == 2 and os.environ.get("MATTERVIS_REQUIRE_BROWSER") != "1":
        pytest.skip(result.stderr.strip() or "browser gate unavailable")
    if result.returncode != 0:
        pytest.fail((result.stderr or result.stdout or "layout browser check failed")[-4000:])
