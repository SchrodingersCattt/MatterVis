"""Compare a publication drawing with the picture on screen.

The screenshot has to contain the paper image's own ink. A dark border or a
title bar can pass a pixel-count threshold without containing that drawing.
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


def _best_fraction(paper: np.ndarray, shot: np.ndarray) -> float:
    paper_cells = int(paper.sum())
    if paper_cells == 0:
        return 0.0
    best = 0.0
    shot_h, shot_w = shot.shape
    for numerator in range(12, 21):
        scale = numerator / 20
        height = max(1, int(round(paper.shape[0] * scale)))
        width = max(1, int(round(paper.shape[1] * scale)))
        if height < 4 or width < 4:
            continue
        scaled = paper if (height, width) == paper.shape else _resize(paper, height, width)
        scaled_cells = int(scaled.sum())
        if scaled_cells == 0:
            continue
        for y0 in range(shot_h - height + 1):
            for x0 in range(shot_w - width + 1):
                overlap = int(np.logical_and(shot[y0 : y0 + height, x0 : x0 + width], scaled).sum())
                best = max(best, overlap / scaled_cells)
                if best >= _OVERLAP:
                    return best
    return best


def picture_report(paper_png: bytes, shot_png: bytes) -> dict[str, float | bool]:
    paper = _pool(_ink(Image.open(io.BytesIO(paper_png))))
    shot = _pool(_ink(Image.open(io.BytesIO(shot_png))))
    paper_cells = int(paper.sum())
    fraction = _best_fraction(paper, shot)
    return {
        "holds": bool(paper_cells >= 2 and fraction >= _OVERLAP),
        "overlap": fraction,
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
