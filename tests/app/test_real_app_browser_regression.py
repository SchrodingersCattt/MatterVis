"""Real MatterVis browser regressions for reset-zoom and scene switching.

The synthetic three-point Plotly page cannot see orthographic aspect-ratio
zoom or a frozen canvas after a tab change and upload. These tests start the
app and drive those sequences.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests.app.real_app_server import bind_failed, start_mattervis
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
# The restart test rebinds one known port after the first listener has
# stopped. Five seconds bounds that retry; it is not a scene-readiness sleep.
_PORT_REBIND_DEADLINE_SECONDS = 5.0
_BROWSER_CIFS = [
    str(ROOT / "docs/images/showcase_dap7.cif"),
    str(ROOT / "scripts/data/DAP-4.cif"),
    str(ROOT / "benchmarks/structures/molecular_water.cif"),
    str(ROOT / "benchmarks/structures/ionic_nacl.cif"),
]


def _require_browser():
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("real-app browser gate needs node, Chrome/Chromium, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"
    return node, chrome, playwright


def _artifact_dir() -> Path:
    current = os.environ.get("PYTEST_CURRENT_TEST", "browser-case")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", current.split(" (", 1)[0])[-180:]
    path = ROOT / "browser-artifacts" / slug
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _write_streams(directory: Path, stdout: str, stderr: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "node-stdout.txt").write_text(stdout or "", encoding="utf-8")
    (directory / "node-stderr.txt").write_text(stderr or "", encoding="utf-8")


def _fail_browser(directory: Path, stdout: str, stderr: str, fallback: str) -> None:
    _write_streams(directory, stdout, stderr)
    tail = (stderr or stdout or fallback)[-4000:]
    pytest.fail(f"{tail}\nfull browser logs: {directory}")


@pytest.fixture
def mattervis_url(tmp_path):
    """One app, scene store, and port for this test only."""
    node, chrome, playwright = _require_browser()
    running = start_mattervis(str(tmp_path), _BROWSER_CIFS)
    try:
        yield running.url, node, chrome, playwright
    finally:
        running.stop()


@pytest.fixture
def fresh_mattervis_url(mattervis_url):
    """Same isolation as ``mattervis_url``. The name marks a default-view case."""
    yield mattervis_url


def _run(mattervis_url, mode: str, structure: str = "", upload: str = "", timeout: int = 160) -> None:
    url, node, chrome, playwright = mattervis_url
    artifacts = _artifact_dir()
    env = {
        **os.environ,
        "NODE_PATH": str(ROOT / "node_modules"),
        "MATTERVIS_BROWSER_ARTIFACTS": str(artifacts),
    }
    try:
        result = subprocess.run(
            [node, str(RUNNER), playwright, chrome, url, mode, upload, structure],
            capture_output=True,
            text=True,
            timeout=timeout,
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
        _fail_browser(artifacts, out, err, "browser regression timed out")
    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        _fail_browser(artifacts, result.stdout, result.stderr, "browser regression failed")
    _write_streams(artifacts, result.stdout, result.stderr)


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
def test_transform_buttons_keep_formula_unit_and_show_warning(fresh_mattervis_url):
    _run(fresh_mattervis_url, "formula-unit-transforms", timeout=160)


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
def test_second_client_plots_the_scene_after_the_other_clients_camera_change(mattervis_url):
    """Client B's canvas follows S after A changes S while B is still on T."""
    _run(mattervis_url, "cross-client", timeout=160)


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


@pytest.mark.timeout(180)
def test_drag_survives_a_frame_that_was_copied_before_the_orbit(mattervis_url):
    """A frame that captured the pre-drag eye must not erase the orbit.

    The copy happens before the pointer moves. The frame paints while the
    button is still down. The eye has to stay on the orbit for the same
    scene generation and server session.
    """
    _run(mattervis_url, "drag-replot", structure="DAP-4", timeout=160)


@pytest.mark.timeout(180)
def test_color_scheme_matches_workbench_and_native_controls(mattervis_url):
    """Light and dark preferences keep chrome and native controls together.

    An explicit data-mattervis-theme pin overrides the operating system.
    """
    _run(mattervis_url, "theme")


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


def _rebind(root: Path, cif_paths: list[str], port: int):
    """Bind ``port`` again after its previous listener has stopped."""
    deadline = time.monotonic() + _PORT_REBIND_DEADLINE_SECONDS
    last_error: BaseException | None = None
    while True:
        try:
            return start_mattervis(str(root), cif_paths, port=port)
        except BaseException as exc:
            if not bind_failed(exc):
                raise
            last_error = exc
            if time.monotonic() >= deadline:
                raise last_error


@pytest.mark.timeout(420)
def test_persisted_store_survives_restart_and_reconnect(tmp_path):
    node, chrome, playwright = _require_browser()
    root = tmp_path / "persisted"
    root.mkdir()
    cifs = [
        str(ROOT / "docs/images/showcase_dap7.cif"),
        str(ROOT / "scripts/data/DAP-4.cif"),
    ]
    upload = str(ROOT / "benchmarks/structures/porous_framework.cif")
    running = start_mattervis(str(root), cifs)
    ready = tmp_path / "restart-ready"
    artifacts = _artifact_dir()
    proc = subprocess.Popen(
        [
            node,
            str(RUNNER),
            playwright,
            chrome,
            running.url,
            "restart",
            upload,
            "DAP-4",
            str(ready),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(ROOT),
        env={
            **os.environ,
            "NODE_PATH": str(ROOT / "node_modules"),
            "MATTERVIS_BROWSER_ARTIFACTS": str(artifacts),
        },
    )
    restarted = None
    first_stopped = False
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and not ready.exists():
            if proc.poll() is not None:
                out, err = proc.communicate()
                _fail_browser(artifacts, out, err, "browser exited before restart")
            time.sleep(0.05)
        if not ready.exists():
            pytest.fail("browser did not reach the persisted-scene restart point")
        port = running.port
        first_stopped = True
        running.stop()
        restarted = _rebind(root, cifs, port)
        try:
            out, err = proc.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            _fail_browser(artifacts, out, err, "restart browser regression timed out")
        if proc.returncode != 0:
            _fail_browser(artifacts, out, err, "restart browser regression failed")
        _write_streams(artifacts, out, err)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
        if not first_stopped:
            running.stop()
        if restarted is not None:
            restarted.stop()


@pytest.mark.timeout(180)
def test_visible_mesh_and_bfdh_faces_share_native_picking(fresh_mattervis_url):
    _run(fresh_mattervis_url, "surface-pick", structure="DAP-4")


@pytest.mark.timeout(180)
def test_projection_radio_follows_scene_and_rejects_stale_store(mattervis_url):
    _run(mattervis_url, "projection-radio")


@pytest.mark.timeout(180)
@pytest.mark.parametrize("transport", ["websocket", "http"])
def test_mixed_projection_and_opacity_update_reaches_the_canvas(fresh_mattervis_url, transport):
    mode = "mixed-projection-http" if transport == "http" else "mixed-projection"
    _run(fresh_mattervis_url, mode)
