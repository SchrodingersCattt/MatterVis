"""Browser regression: a single-point structure opens and the camera still moves."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from mat_viewer.app.factory import create_app
from tests.app.test_camera_browser_regression import _chrome, _playwright_module, _unavailable
from tests.app.test_real_app_browser_regression import _artifact_dir, _fail_browser, _write_streams

pytestmark = [
    pytest.mark.browser,
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "single_point_view_browser.js"
OCTAHEDRON = ROOT / "tests" / "fixtures" / "synthetic_ordered_octahedron.cif"

_SPREAD = """data_spread
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
C1 C 0.20 0.20 0.20 1
C2 C 0.40 0.22 0.25 1
C3 C 0.28 0.45 0.30 1
O1 O 0.55 0.40 0.48 1
"""


def _require_browser():
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("single-point browser gate needs node, Chrome/Chromium, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"
    return node, chrome, playwright


@pytest.mark.timeout(180)
def test_single_point_structure_camera_survives_drag_reset_and_scene_switch(tmp_path: Path):
    node, chrome, playwright = _require_browser()
    spread = tmp_path / "spread.cif"
    spread.write_text(_SPREAD, encoding="utf-8")
    local = tmp_path / ".local"
    local.mkdir()
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    "ordered_octahedron": {
                        "cif_path": str(OCTAHEDRON),
                        "title": "ordered_octahedron",
                    },
                    "spread": {"cif_path": str(spread), "title": "spread"},
                }
            }
        ),
        encoding="utf-8",
    )
    app = create_app(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    server = make_server("127.0.0.1", 0, app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    artifacts = _artifact_dir()
    env = {**os.environ, "NODE_PATH": str(ROOT / "node_modules")}
    try:
        result = subprocess.run(
            [node, str(RUNNER), playwright, chrome, url],
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
        _fail_browser(artifacts, out, err, "single-point browser regression timed out")
        return
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        backend = getattr(app, "crystal_backend", None)
        if backend is not None:
            backend.close()
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        _fail_browser(artifacts, result.stdout, result.stderr, "single-point browser regression failed")
    _write_streams(artifacts, result.stdout, result.stderr)
