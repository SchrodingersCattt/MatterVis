"""The publication watcher has to reject a drawing that disappears later."""

from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tests.app.flat_ortep_picture import picture_holds


def _png(width: int, height: int, paint) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    paint(ImageDraw.Draw(image))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _disk(width: int = 200, height: int = 200) -> bytes:
    return _png(width, height, lambda draw: draw.ellipse((60, 60, 140, 140), fill=(180, 40, 40)))


def test_shifted_drawing_still_holds():
    paper = _disk()
    shifted = _png(240, 240, lambda draw: draw.ellipse((80, 30, 160, 110), fill=(180, 40, 40)))
    assert picture_holds(paper, shifted)


def test_matching_drawing_holds_and_chrome_does_not():
    paper = _png(1000, 700, lambda draw: draw.ellipse((300, 180, 700, 520), fill=(180, 40, 40)))
    assert picture_holds(paper, paper)
    frame = _png(1000, 700, lambda draw: draw.rectangle((0, 0, 999, 699), outline=(0, 0, 0), width=2))
    title = _png(1000, 700, lambda draw: draw.rectangle((20, 8, 460, 32), fill=(20, 20, 20)))
    assert not picture_holds(paper, frame)
    assert not picture_holds(paper, title)
    assert not picture_holds(paper, _png(1000, 700, lambda draw: None))


def test_watcher_rejects_a_later_loss_and_a_single_good_frame():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the picture watcher")
    source = Path(__file__).resolve().parent / "flat_ortep_picture.js"
    program = r"""
const assert = require('node:assert/strict');
const {noteSample, accepted} = require(process.argv[1]);
function replay(samples) {
  return samples.reduce((state, sample) => noteSample(state, sample), {ready:false, seenLater:false, failed:''});
}
const good = {holds:true, statusBad:false};
assert.equal(accepted(replay([good])), false, 'the first good frame returned early');
assert.equal(replay([good, good, {holds:false, statusBad:false}]).failed, 'disappeared');
assert.equal(replay([good, good, {holds:true, statusBad:true}]).failed, 'status');
assert.equal(accepted(replay([good, good, good])), true);
assert.equal(replay([{holds:false, statusBad:false}, {holds:false, statusBad:false}]).ready, false);
"""
    result = subprocess.run([node, "-e", program, str(source)], capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr