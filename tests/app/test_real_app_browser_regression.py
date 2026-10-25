"""Real MatterVis browser regressions for reset-zoom and scene switching.

The synthetic three-point Plotly page cannot see orthographic aspect-ratio
zoom or a frozen canvas after a tab change and upload. These tests start the
app and drive those sequences.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from mat_viewer.app.factory import create_app
from tests.app.test_camera_browser_regression import (
    _chrome,
    _playwright_module,
    _unavailable,
)


pytestmark = [
    pytest.mark.browser,
    pytest.mark.filterwarnings("ignore:Structure contains disorder:UserWarning"),
    pytest.mark.filterwarnings("ignore::ResourceWarning"),
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "real_app_browser.js"


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _mattervis_server(tmp_path_factory):
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("real-app browser gate needs node, Chrome/Chromium, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"

    root = tmp_path_factory.mktemp("mattervis-browser")
    app = create_app(
        root_dir=str(root),
        cif_paths=[
            str(ROOT / "docs/images/showcase_dap7.cif"),
            str(ROOT / "scripts/data/DAP-4.cif"),
            str(ROOT / "benchmarks/structures/molecular_water.cif"),
            str(ROOT / "benchmarks/structures/ionic_nacl.cif"),
        ],
    )
    server = make_server("127.0.0.1", _port(), app.server, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", node, chrome, playwright
    finally:
        server.shutdown()
        backend = getattr(app, "crystal_backend", None)
        if backend is not None:
            backend.close()


@pytest.fixture(scope="module")
def mattervis_url(tmp_path_factory):
    yield from _mattervis_server(tmp_path_factory)


@pytest.fixture
def fresh_mattervis_url(tmp_path_factory):
    # The surface fixture assumes the default DAP-4 view. Earlier drag tests
    # persist a different camera in the shared module server and can put the
    # test meshes off-screen, despite starting a fresh browser context.
    yield from _mattervis_server(tmp_path_factory)


def _run(mattervis_url, mode: str, structure: str = "", upload: str = "", timeout: int = 160) -> None:
    url, node, chrome, playwright = mattervis_url
    try:
        result = subprocess.run(
            [node, str(RUNNER), playwright, chrome, url, mode, upload, structure],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(ROOT),
            env={**os.environ, "NODE_PATH": str(ROOT / "node_modules")},
        )
    except subprocess.TimeoutExpired as exc:
        err = (exc.stderr or b"") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        out = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        pytest.fail((err or out or "browser regression timed out")[-4000:])
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        pytest.fail((result.stderr or result.stdout or "browser regression failed")[-4000:])


@pytest.mark.timeout(180)
@pytest.mark.parametrize("structure", ["ionic_nacl", "DAP-4"])
def test_reset_clears_orthographic_wheel_zoom(mattervis_url, structure):
    _run(mattervis_url, "reset", structure=structure)


@pytest.mark.timeout(180)
def test_scene_switch_and_upload_replace_the_canvas(mattervis_url):
    upload = ROOT / "benchmarks/structures/porous_framework.cif"
    _run(mattervis_url, "switch", upload=str(upload))


@pytest.mark.timeout(180)
def test_duplicate_and_rename_scene_controls_update_canvas(mattervis_url):
    """Duplicate/rename controls select the new tab and repaint its title."""
    _run(mattervis_url, "scene-controls")


@pytest.mark.timeout(180)
def test_narrow_layout_keeps_tools_tabs_and_log_usable(mattervis_url):
    """Check item (19) against real Dash components, not just a CSS fixture."""
    _run(mattervis_url, "narrow-layout")


@pytest.mark.timeout(180)
def test_fit_align_orbit_and_pan_keep_a_visible_projection(mattervis_url):
    _run(mattervis_url, "gestures", structure="ionic_nacl")


@pytest.mark.timeout(180)
def test_held_drag_cannot_overwrite_reset(mattervis_url):
    _run(mattervis_url, "held", structure="ionic_nacl")


@pytest.mark.timeout(180)
def test_rapid_scene_switch_during_drag(mattervis_url):
    _run(mattervis_url, "rapid")


@pytest.mark.timeout(180)
def test_two_browsers_keep_separate_scenes_during_upload(mattervis_url):
    upload = ROOT / "benchmarks/structures/porous_framework.cif"
    _run(mattervis_url, "sessions", upload=str(upload))


@pytest.mark.timeout(180)
def test_drag_on_one_scene_does_not_reframe_ionic_nacl(mattervis_url):
    _run(mattervis_url, "nacl-frame")


@pytest.mark.timeout(240)
def test_orthographic_unit_cell_drag_keeps_dap4_visible(mattervis_url):
    _run(mattervis_url, "ortho-drag", structure="DAP-4", timeout=220)


@pytest.mark.timeout(240)
def test_semantic_atom_click_survives_gl_fallback(mattervis_url):
    """A native click keeps the atom and an additive non-atom target.

    DAP-4 has stable covalent bond selection markers in its initial figure;
    ionic NaCl intentionally suppresses bond traces and computes polyhedra
    asynchronously, which makes it a poor deterministic fixture for the
    second half of this gesture regression.
    """
    _run(mattervis_url, "semantic-pick", structure="DAP-4", timeout=220)


@pytest.mark.timeout(240)
def test_one_hundred_scene_switches_do_not_leak_sockets(mattervis_url):
    _run(mattervis_url, "leak", timeout=220)


def _start_server(app, port: int):
    last_error = None
    for _ in range(20):
        try:
            server = make_server("127.0.0.1", port, app.server, threaded=True)
        except OSError as exc:
            last_error = exc
            time.sleep(0.25)
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    raise last_error


@pytest.mark.timeout(420)
def test_persisted_store_survives_restart_and_reconnect(tmp_path):
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("real-app browser gate needs node, Chrome/Chromium, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"
    root = tmp_path / "persisted"
    root.mkdir()
    cifs = [
        str(ROOT / "docs/images/showcase_dap7.cif"),
        str(ROOT / "scripts/data/DAP-4.cif"),
    ]
    upload = str(ROOT / "benchmarks/structures/porous_framework.cif")
    port = _port()
    ready = tmp_path / "restart-ready"
    app = create_app(root_dir=str(root), cif_paths=cifs)
    server = _start_server(app, port)
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [node, str(RUNNER), playwright, chrome, url, "restart", upload, "DAP-4", str(ready)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(ROOT),
        env={**os.environ, "NODE_PATH": str(ROOT / "node_modules")},
    )
    app2 = None
    server2 = None
    try:
        deadline = time.time() + 180
        while time.time() < deadline and not ready.exists():
            if proc.poll() is not None:
                out, err = proc.communicate()
                pytest.fail((err or out or "browser exited before restart")[-4000:])
            time.sleep(0.25)
        if not ready.exists():
            pytest.fail("browser did not reach the persisted-scene restart point")
        server.shutdown()
        app.crystal_backend.close()
        app2 = create_app(root_dir=str(root), cif_paths=cifs)
        server2 = _start_server(app2, port)
        try:
            out, err = proc.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            pytest.fail((err or out or "restart browser regression timed out")[-4000:])
        if proc.returncode != 0:
            pytest.fail((err or out or "restart browser regression failed")[-4000:])
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        try:
            server.shutdown()
        except Exception:
            pass
        if server2 is not None:
            server2.shutdown()
        for viewer in (app, app2):
            backend = getattr(viewer, "crystal_backend", None)
            if backend is not None:
                backend.close()


@pytest.mark.timeout(180)
def test_visible_mesh_and_bfdh_faces_share_native_picking(fresh_mattervis_url):
    _run(fresh_mattervis_url, "surface-pick", structure="DAP-4")
