"""A failing painted margin keeps the screenshot and the camera record."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from tests.app.fit_gap_artifacts import measure_gap, preserve_browser_files, write_gap_artifacts
from tests.app.test_fit_projection_browser import _assert_painted_gaps


def _frame(path: Path) -> dict:
    image = Image.new("RGB", (200, 100), (255, 255, 255))
    pixels = image.load()
    for y in range(90, 100):
        for x in range(40, 160):
            pixels[x, y] = (20, 20, 20)
    for y in range(80, 100):
        for x in range(0, 28):
            pixels[x, y] = (0, 0, 0)
    image.save(path)
    return {
        "file": path.name,
        "plot": {"x": 0, "y": 0, "width": 200, "height": 100},
        "canvas": {"x": 0, "y": 0, "width": 200, "height": 120},
        "chrome": [{"id": "canvas-view-fit", "x": 0, "y": 80, "width": 30, "height": 20}],
        "projection": "perspective",
        "eye": [0.5, 0.5, 0.5],
        "center": [0.0, 0.0, 0.0],
        "aspect": {"x": 1, "y": 0.26, "z": 0.26},
        "ranges": {"x": [0, 25.6], "y": [0, 6.65], "z": [0, 6.65]},
        "revision": 4,
        "renderRevision": 2,
        "sceneId": "scene-elongated",
        "sceneGeneration": "3",
        "geometryVersion": 1,
        "coordinateDigest": "scatter3d:0,0,0;1,1,1",
        "before": {
            "eye": [0.5, 0.5, 0.5],
            "sceneGeneration": "3",
            "geometryVersion": 1,
            "coordinateDigest": "scatter3d:0,0,0;1,1,1",
            "chrome": [{"id": "canvas-view-fit", "x": 0, "y": 80, "width": 30, "height": 20}],
        },
    }


def test_failing_margin_keeps_png_mask_and_camera(tmp_path: Path):
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    shot = _frame(shots_dir / "elongated-1280-perspective.png")
    record = measure_gap(shot, shots_dir)
    assert record["failed"] is True
    saved = write_gap_artifacts(tmp_path / "out", [shot], shots_dir, [record])
    original = saved / "elongated-1280-perspective.png"
    assert original.read_bytes() == (shots_dir / shot["file"]).read_bytes()
    assert (saved / "elongated-1280-perspective-mask.png").stat().st_size > 0
    assert (saved / "elongated-1280-perspective-bottom.png").stat().st_size > 0
    payload = json.loads((saved / "elongated-1280-perspective.json").read_text(encoding="utf-8"))
    assert payload["after"]["eye"] == [0.5, 0.5, 0.5]
    assert payload["after"]["sceneGeneration"] == "3"
    assert payload["after"]["geometryVersion"] == 1
    assert payload["after"]["coordinateDigest"] == "scatter3d:0,0,0;1,1,1"
    assert payload["after"]["revision"] == 4
    assert payload["image_size"] == [200, 100]
    assert payload["gaps_left_right_top_bottom"][3] < payload["pad"]
    lowest = payload["lowest_counted_ink"]
    assert lowest
    assert all(pixel["x"] >= 40 for pixel in lowest)
    assert all(pixel["chrome"]["id"] == "canvas-view-fit" for pixel in lowest)
    mask = Image.open(saved / "elongated-1280-perspective-mask.png").convert("RGB")
    assert mask.getpixel((50, 95)) == (255, 0, 255)
    assert mask.getpixel((10, 90)) == (255, 140, 0)
    assert mask.getpixel((100, 10)) == (0, 0, 0)


def test_assert_writes_the_ci_artifact_directory(tmp_path: Path):
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    shot = _frame(shots_dir / "elongated-1280-perspective.png")
    with pytest.raises(AssertionError, match="artifacts:") as caught:
        _assert_painted_gaps([shot], shots_dir)
    saved = Path(str(caught.value).split("artifacts:", 1)[1].strip().splitlines()[0].strip())
    payload = json.loads((saved / "elongated-1280-perspective.json").read_text(encoding="utf-8"))
    assert payload["after"]["coordinateDigest"] == "scatter3d:0,0,0;1,1,1"
    assert payload["after"]["sceneGeneration"] == "3"
    assert (saved / "elongated-1280-perspective.png").is_file()
    assert (saved / "elongated-1280-perspective-mask.png").is_file()


def test_empty_ink_keeps_the_png_and_fails(tmp_path: Path):
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    Image.new("RGB", (80, 40), (255, 255, 255)).save(shots_dir / "blank.png")
    shot = {
        "file": "blank.png",
        "plot": {"x": 0, "y": 0, "width": 80, "height": 40},
        "chrome": [],
        "eye": [1, 1, 1],
        "sceneGeneration": "1",
        "geometryVersion": 0,
        "coordinateDigest": "",
    }
    record = measure_gap(shot, shots_dir)
    assert record["failed"] is True
    assert record["empty_ink"] is True
    saved = write_gap_artifacts(tmp_path / "out", [shot], shots_dir, [record])
    assert (saved / "blank.png").read_bytes() == (shots_dir / "blank.png").read_bytes()
    payload = json.loads((saved / "blank.json").read_text(encoding="utf-8"))
    assert payload["empty_ink"] is True
    assert payload["gaps_left_right_top_bottom"] is None
    assert payload["after"]["sceneGeneration"] == "1"


def test_node_failure_keeps_partial_screenshots(tmp_path: Path):
    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    (shots_dir / "partial.png").write_bytes(b"png")
    (shots_dir / "matrix-fit-timeout.json").write_text("{}", encoding="utf-8")
    saved = preserve_browser_files(
        tmp_path / "out",
        shots_dir,
        reason="node",
        stdout="stdout",
        stderr="stderr",
    )
    assert (saved / "partial.png").read_bytes() == b"png"
    assert (saved / "matrix-fit-timeout.json").read_text(encoding="utf-8") == "{}"
    assert json.loads((saved / "reason.json").read_text(encoding="utf-8"))["reason"] == "node"
    assert (saved / "stderr.txt").read_text(encoding="utf-8") == "stderr"
