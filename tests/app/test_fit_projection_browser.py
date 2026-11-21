"""Painted Fit camera at the workbench plot and at wide and tall windows."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.app.fit_gap_artifacts import (
    ink_box,
    measure_gap,
    preserve_browser_files,
    write_gap_artifacts,
)
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
WATER = ROOT / "benchmarks" / "structures" / "molecular_water.cif"
RUNNER = Path(__file__).resolve().parent / "fit_projection_browser.js"
MATRIX_RUNNER = Path(__file__).resolve().parent / "fit_projection_matrix_browser.js"
READBACK_RUNNER = Path(__file__).resolve().parent / "fit_projection_readback_browser.js"
TRICLINIC = """data_triclinic
_cell_length_a 7.0
_cell_length_b 8.2
_cell_length_c 9.4
_cell_angle_alpha 80
_cell_angle_beta 75
_cell_angle_gamma 70
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
C1 C 0.20 0.30 0.40 1
O1 O 0.55 0.42 0.61 1
"""
def _ink_box(path: Path, plot: dict, chrome: list[dict]) -> tuple[int, int, int, int, dict]:
    # White is empty canvas. Gray cell edges, pale hydrogen spheres,
    # and colored atoms are structure. Chrome rectangles are excluded
    # separately, from the rects captured with this frame.
    return ink_box(path, plot, chrome)


def _fit_artifact_dir() -> Path:
    current = os.environ.get("PYTEST_CURRENT_TEST", "fit-painted-gap")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", current.split(" (", 1)[0])[-180:]
    path = ROOT / "browser-artifacts" / slug
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


_RESTORE_STATUS = (
    "could not be restored",
    "did not match the view",
    "Restoring the current view",
    "Restored the current camera view",
)


def _toast_problems(shots):
    problems = []
    for shot in shots:
        status = str(shot.get("status") or "")
        if any(phrase in status for phrase in _RESTORE_STATUS):
            problems.append(
                {
                    "file": shot.get("file"),
                    "status": status,
                    "postcondition": shot.get("postcondition"),
                    "eye": shot.get("eye"),
                    "center": shot.get("center"),
                    "ranges": shot.get("ranges"),
                    "aspect": shot.get("aspect"),
                }
            )
        for line in shot.get("console") or []:
            text = str(line)
            if "camera postcondition" in text or "could not be restored" in text:
                problems.append({"file": shot.get("file"), "console": text})
    return problems


def _gap_records(shots, shots_dir: Path) -> list[dict]:
    # Left matches right and top matches bottom. The two axes need
    # not match each other: a long cell in a square window has a
    # larger gap on the short axis. The numbers live in measure_gap.
    return [measure_gap(shot, shots_dir) for shot in shots]


def _assert_painted_gaps(shots, shots_dir: Path) -> None:
    toasts = _toast_problems(shots)
    records = _gap_records(shots, shots_dir)
    problems = [record["problem"] for record in records if record["failed"]]
    artifact = None
    if problems or toasts:
        artifact = _fit_artifact_dir()
        if problems:
            write_gap_artifacts(artifact, shots, shots_dir, records)
        preserve_browser_files(
            artifact,
            shots_dir,
            reason="painted margin" if problems else "toast",
        )
    message = "\n".join(str(item) for item in [*toasts, *problems])
    if artifact is not None:
        message = f"{message}\nartifacts: {artifact}"
    assert not toasts and not problems, message


def _run_browser(tmp_path, runner: Path, cifs: list[str]):
    node = shutil.which("node")
    chrome = _chrome()
    playwright = _playwright_module(node) if node else None
    if not node or not chrome or not playwright:
        _unavailable("fit browser test needs node, Chrome, and Playwright")
    os.environ["MATTERVIS_PREWARM"] = "0"
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    running = start_mattervis(str(tmp_path), cifs)
    try:
        try:
            result = subprocess.run(
                [node, str(runner), playwright, chrome, running.url, str(shots_dir)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                cwd=str(ROOT),
                env={**os.environ, "NODE_PATH": str(ROOT / "node_modules")},
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            saved = preserve_browser_files(
                _fit_artifact_dir(),
                shots_dir,
                reason="timeout",
                stdout=stdout,
                stderr=stderr,
            )
            pytest.fail(f"fit browser test timed out\nartifacts: {saved}")
    finally:
        running.stop()
    if result.returncode != 0:
        saved = preserve_browser_files(
            _fit_artifact_dir(),
            shots_dir,
            reason="node",
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )
        tail = (result.stderr or result.stdout or "fit browser test failed")[-4000:]
        pytest.fail(f"{tail}\nartifacts: {saved}")
    return json.loads(result.stdout.strip().splitlines()[-1]), shots_dir


def test_painted_fit_frames_the_cell(tmp_path):
    shots, shots_dir = _run_browser(tmp_path, RUNNER, [str(NACL)])
    assert len(shots) == 6
    assert all(shot.get("boundaryChecked") is True for shot in shots)
    _assert_painted_gaps(shots, shots_dir)


def test_painted_fit_frames_elongated_triclinic_and_water(tmp_path):
    triclinic = tmp_path / "triclinic.cif"
    triclinic.write_text(TRICLINIC, encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    shots, shots_dir = _run_browser(
        run,
        MATRIX_RUNNER,
        [str(NACL), str(WATER), str(triclinic)],
    )
    assert len(shots) == 9
    assert all(shot.get("boundaryChecked") is shot["boundary"] for shot in shots)
    _assert_painted_gaps(shots, shots_dir)
    elongated = [shot for shot in shots if shot["file"].startswith("elongated-")]
    assert elongated
    for shot in elongated:
        span = abs(shot["ranges"]["x"][1] - shot["ranges"]["x"][0])
        assert 18.0 < span < 32.0, (shot["file"], span)


def _same_frame(left, right) -> bool:
    eye = sum((left["eye"][index] - right["eye"][index]) ** 2 for index in range(3)) ** 0.5
    length = sum(value * value for value in left["eye"]) ** 0.5
    if eye / max(length, 1e-6) > 0.02:
        return False
    if abs(left["aspect"] - right["aspect"]) / max(abs(left["aspect"]), 1e-6) > 0.01:
        return False
    for before, after in zip(left["spans"], right["spans"]):
        if abs(before - after) / max(abs(before), 1e-6) > 0.01:
            return False
    return True


def test_fit_readback_survives_figure_react_tab_roundtrip_and_reset(tmp_path):
    """The fitted frame survives a figure rebuild, a tab return, and Reset.

    Synthetic NaCl fills its cube. With the boundary on, the framed box is
    the cell corners plus the drawn radii, span 6.944. With the boundary
    off, those same atoms still run from one corner of that cube to the
    other, so the span stays 6.944. The contract is the painted geometry:
    the original 8% margin and opposite-gap centering, and the same eye,
    aspect, and ranges after a rebuild and a tab return. A scalar span
    change is not required for this fixture. Boundary on and boundary off
    stay separate painted cases in the matrix.
    """
    report, shots_dir = _run_browser(tmp_path, READBACK_RUNNER, [str(NACL)])
    assert not _toast_problems([report]), _toast_problems([report])
    assert report["boundaryOn"] is True
    assert report["boundaryOff"] is False
    assert _same_frame(report["fitted"], report["reacted"]), (report["fitted"], report["reacted"])
    assert _same_frame(report["fitted"], report["returned"]), (report["fitted"], report["returned"])
    assert _same_frame(report["cluster"], report["clusterReact"]), (report["cluster"], report["clusterReact"])
    assert report["cluster"]["spans"][0] < 9.0
    assert report["reset"]["spans"][0] < 9.0
    shot = {"file": report["file"], "plot": report["plot"], "chrome": report["chrome"]}
    x0, y0, x1, y1, visible = _ink_box(shots_dir / shot["file"], shot["plot"], shot["chrome"])
    assert x0 is not None, shot["file"]
    gaps = (
        x0 - visible["x"],
        visible["x"] + visible["width"] - x1,
        y0 - visible["y"],
        visible["y"] + visible["height"] - y1,
    )
    assert min(gaps) >= 8, gaps
