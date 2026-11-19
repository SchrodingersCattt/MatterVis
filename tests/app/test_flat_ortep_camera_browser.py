"""Browser regression: flat ORTEP keeps a usable camera on the real viewer."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.app.real_app_server import start_mattervis
from tests.app.test_camera_browser_regression import _chrome, _playwright_module, _unavailable
from tests.app.test_real_app_browser_regression import _artifact_dir, _fail_browser, _write_streams

pytestmark = [
    pytest.mark.browser,
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "flat_ortep_camera_browser.js"

_CIF = """data_{name}
_cell_length_a 12
_cell_length_b 12
_cell_length_c 12
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
_atom_site_U_iso_or_equiv
C1 C {x0} 0.50 0.50 1 0.04
C2 C 0.52 0.50 0.50 1 0.05
C3 C 0.58 0.60 0.50 1 0.04
O1 O 0.58 0.70 0.50 1 0.06
"""


def _require_browser():
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("flat ORTEP browser gate needs node, Chrome/Chromium, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"
    return node, chrome, playwright


@pytest.mark.timeout(180)
def test_flat_ortep_camera_survives_drag_reset_and_scene_switch(tmp_path: Path):
    node, chrome, playwright = _require_browser()
    alpha = tmp_path / "alpha.cif"
    beta = tmp_path / "beta.cif"
    alpha.write_text(_CIF.format(name="alpha", x0="0.40"), encoding="utf-8")
    beta.write_text(_CIF.format(name="beta", x0="0.30"), encoding="utf-8")
    running = start_mattervis(str(tmp_path), [str(alpha), str(beta)])
    artifacts = _artifact_dir()
    env = {**os.environ, "NODE_PATH": str(ROOT / "node_modules"), "MV_PYTHON": sys.executable}
    try:
        result = subprocess.run(
            [node, str(RUNNER), playwright, chrome, running.url],
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
            cwd=str(ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr or ""
        out = exc.stdout or ""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        _fail_browser(artifacts, out, err, "flat ORTEP browser regression timed out")
        return
    finally:
        running.stop()
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        _fail_browser(artifacts, result.stdout, result.stderr, "flat ORTEP browser regression failed")
    _write_streams(artifacts, result.stdout, result.stderr)
