"""Runtime Commit path: 8-site copy, then 16, then 128, on that page only."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.app.real_app_server import start_mattervis
from tests.app.test_camera_browser_regression import (
    _chrome,
    _playwright_module,
    _unavailable,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

ROOT = Path(__file__).resolve().parents[2]
NACL = ROOT / "tests" / "fixtures" / "structures" / "synthetic_nacl.cif"
RUNNER = Path(__file__).resolve().parent / "supercell_commit_browser.js"


def test_commit_selects_16_then_128_without_moving_another_page(tmp_path):
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("supercell commit browser test needs node, Chrome, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"
    running = start_mattervis(str(tmp_path), [str(NACL)])
    try:
        env = {**os.environ, "NODE_PATH": str(ROOT / "node_modules")}
        result = subprocess.run(
            [node, str(RUNNER), playwright, chrome, running.url],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
            cwd=str(ROOT),
            env=env,
        )
    finally:
        running.stop()
    if result.returncode != 0:
        pytest.fail((result.stderr or result.stdout or "commit browser test failed")[-4000:])
    assert "scene_" in result.stdout
