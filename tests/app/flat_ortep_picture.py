"""Compare a publication drawing with the picture on screen.

The paper image's ink has to land on ink, and its white cells have to stay
white. A black screen covers the ink and also covers the white, so overlap
of the ink alone is not enough. The pixels are the layout image currently
displayed. This does not identify that image with a scene, generation, or
revision.
"""

from __future__ import annotations

import io
import sys

import numpy as np
from PIL import Image

_GRID = 24
_INSET = 0.02
_OVERLAP = 0.5


def _ink(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"))
    ink = np.any(rgb < 245, axis=2)
    height, width = ink.shape
    inset_x = max(1, int(round(width * _INSET)))
    inset_y = max(1, int(round(height * _INSET)))
    ink[:inset_y, :] = False
    ink[-inset_y:, :] = False
    ink[:, :inset_x] = False
    ink[:, -inset_x:] = False
    return ink


def _pool(mask: np.ndarray, grid: int = _GRID) -> np.ndarray:
    height, width = mask.shape
    cells = np.zeros((grid, grid), dtype=bool)
    for row in range(grid):
        y0 = row * height // grid
        y1 = max(y0 + 1, (row + 1) * height // grid)
        for column in range(grid):
            x0 = column * width // grid
            x1 = max(x0 + 1, (column + 1) * width // grid)
            cells[row, column] = bool(mask[y0:y1, x0:x1].any())
    return cells


def _resize(mask: np.ndarray, height: int, width: int) -> np.ndarray:
    src_h, src_w = mask.shape
    out = np.zeros((height, width), dtype=bool)
    for row in range(height):
        y0 = row * src_h // height
        y1 = max(y0 + 1, (row + 1) * src_h // height)
        for column in range(width):
            x0 = column * src_w // width
            x1 = max(x0 + 1, (column + 1) * src_w // width)
            out[row, column] = bool(mask[y0:y1, x0:x1].any())
    return out


def _best_agreement(paper: np.ndarray, shot: np.ndarray) -> float:
    """Best placement where ink and the paper's own white both agree.

    Ink overlap alone accepts a black screen: every paper ink cell lands on
    dark pixels. The white cells of the paper have to stay white in that
    same window, or the screen is a filled field rather than the drawing.
    """
    best = 0.0
    shot_h, shot_w = shot.shape
    for numerator in range(12, 21):
        height = max(1, int(round(paper.shape[0] * numerator / 20)))
        width = max(1, int(round(paper.shape[1] * numerator / 20)))
        if height < 4 or width < 4:
            continue
        scaled = paper if (height, width) == paper.shape else _resize(paper, height, width)
        ink_cells = int(scaled.sum())
        white_cells = int((~scaled).sum())
        if ink_cells == 0 or white_cells == 0:
            continue
        for y0 in range(shot_h - height + 1):
            for x0 in range(shot_w - width + 1):
                window = shot[y0 : y0 + height, x0 : x0 + width]
                ink_hit = int(np.logical_and(window, scaled).sum()) / ink_cells
                white_hit = int(np.logical_and(~window, ~scaled).sum()) / white_cells
                agreement = min(ink_hit, white_hit)
                if agreement > best:
                    best = agreement
                if best >= _OVERLAP:
                    return best
    return best


def picture_report(paper_png: bytes, shot_png: bytes) -> dict[str, float | bool]:
    paper = _pool(_ink(Image.open(io.BytesIO(paper_png))))
    shot = _pool(_ink(Image.open(io.BytesIO(shot_png))))
    paper_cells = int(paper.sum())
    white_cells = int((~paper).sum())
    agreement = _best_agreement(paper, shot) if paper_cells >= 2 and white_cells >= 2 else 0.0
    return {
        "holds": bool(agreement >= _OVERLAP),
        "overlap": agreement,
        "paper_cells": paper_cells,
    }


def picture_holds(paper_png: bytes, shot_png: bytes) -> bool:
    return bool(picture_report(paper_png, shot_png)["holds"])


def _read_pair(blob: bytes) -> tuple[bytes, bytes]:
    paper_len = int.from_bytes(blob[:4], "big")
    shot_len = int.from_bytes(blob[4:8], "big")
    start = 8
    paper = blob[start : start + paper_len]
    shot = blob[start + paper_len : start + paper_len + shot_len]
    if len(paper) != paper_len or len(shot) != shot_len:
        raise ValueError("truncated picture pair")
    return paper, shot


if __name__ == "__main__":
    report = picture_report(*_read_pair(sys.stdin.buffer.read()))
    print("1" if report["holds"] else "0", report["overlap"], report["paper_cells"])
