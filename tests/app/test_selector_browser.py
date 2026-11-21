"""Real workbench controls for by-element and between-elements rules."""

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
RUNNER = Path(__file__).resolve().parent / "selector_browser.js"


def _write_fixture(directory: Path) -> str:
    target = directory / "mixed_cco.cif"
    target.write_text(
        """data_mixed_cco
_cell_length_a 20
_cell_length_b 20
_cell_length_c 20
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_space_group_name_H-M_alt 'P 1'
loop_
_space_group_symop_operation_xyz
'x, y, z'
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
C1 C 0.400 0.500 0.500 1
C2 C 0.475 0.500 0.500 1
O1 O 0.400 0.570 0.500 1
""",
        encoding="utf-8",
    )
    return str(target)


@pytest.mark.timeout(180)
def test_element_and_endpoint_selectors_round_trip_in_the_browser(tmp_path: Path):
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("selector browser test needs node, Chrome, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"
    running = start_mattervis(str(tmp_path), [_write_fixture(tmp_path)])
    artifacts = ROOT / "browser-artifacts" / "selector-edit-state"
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    try:
        result = subprocess.run(
            [node, str(RUNNER), playwright, chrome, running.url],
            capture_output=True,
            text=True,
            timeout=160,
            check=False,
            cwd=str(ROOT),
            env={
                **os.environ,
                "NODE_PATH": str(ROOT / "node_modules"),
                "MATTERVIS_BROWSER_ARTIFACTS": str(artifacts),
            },
        )
    finally:
        running.stop()
    (artifacts / "node-stdout.txt").write_text(result.stdout or "", encoding="utf-8")
    (artifacts / "node-stderr.txt").write_text(result.stderr or "", encoding="utf-8")
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "selector browser failed")[-4000:]
        pytest.fail(f"{tail}\nfull browser logs: {artifacts}")
    assert "selector controls ok" in result.stdout
