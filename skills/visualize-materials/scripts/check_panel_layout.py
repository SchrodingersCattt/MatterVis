#!/usr/bin/env python3
"""Measure panel ink bounds, occupancy, and safety pads in a PNG.

This checker deliberately uses only raster geometry. It does not claim chemical
or aesthetic validation and is suitable for agents without image understanding.
Panel rectangles may be supplied explicitly or inferred as an equal grid.
"""

from __future__ import annotations

import argparse
import json
from itertools import pairwise
from pathlib import Path

import numpy as np
from PIL import Image


def parse_boundaries(text: str, width: int) -> list[int]:
    values = [int(value.strip()) for value in text.split(",") if value.strip()]
    if len(values) < 2 or values[0] != 0 or values[-1] != width:
        raise ValueError(f"--boundaries must start at 0 and end at image width {width}")
    if any(second <= first for first, second in pairwise(values)):
        raise ValueError("--boundaries must be strictly increasing")
    return values


def equal_boundaries(width: int, panels: int) -> list[int]:
    if panels < 1:
        raise ValueError("--panels must be positive")
    return np.linspace(0, width, panels + 1, dtype=int).tolist()


def parse_rectangles(text: str, width: int, height: int) -> list[list[int]]:
    payload = json.loads(text)
    if not isinstance(payload, list) or not payload:
        raise ValueError("--rectangles must be a non-empty JSON list")
    rectangles = []
    for index, value in enumerate(payload):
        if not isinstance(value, list) or len(value) != 4:
            raise ValueError(f"rectangle {index} must be [x0, y0, x1, y1]")
        rectangle = [int(item) for item in value]
        x0, y0, x1, y1 = rectangle
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise ValueError(f"rectangle {index} is outside the image")
        rectangles.append(rectangle)
    return rectangles


def equal_grid_rectangles(
    width: int, height: int, rows: int, columns: int
) -> list[list[int]]:
    if rows < 1 or columns < 1:
        raise ValueError("--grid rows and columns must be positive")
    xs = equal_boundaries(width, columns)
    ys = equal_boundaries(height, rows)
    return [
        [x0, y0, x1, y1]
        for y0, y1 in pairwise(ys)
        for x0, x1 in pairwise(xs)
    ]


def background_distance(rgb: np.ndarray, background: np.ndarray) -> np.ndarray:
    return np.max(np.abs(rgb.astype(np.int16) - background.astype(np.int16)), axis=2)


def measure_panel(
    rgb: np.ndarray,
    x0: int,
    x1: int,
    *,
    background: np.ndarray,
    tolerance: int,
    min_occupancy: float,
    max_occupancy: float,
    min_pad: int,
) -> dict:
    return measure_rectangle(
        rgb,
        x0,
        0,
        x1,
        rgb.shape[0],
        background=background,
        tolerance=tolerance,
        min_occupancy=min_occupancy,
        max_occupancy=max_occupancy,
        min_pad=min_pad,
    )


def measure_rectangle(
    rgb: np.ndarray,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    background: np.ndarray,
    tolerance: int,
    min_occupancy: float,
    max_occupancy: float,
    min_pad: int,
) -> dict:
    panel = rgb[y0:y1, x0:x1]
    ink = background_distance(panel, background) > tolerance
    ys, xs = np.where(ink)
    width = x1 - x0
    height = y1 - y0
    if len(xs) == 0:
        return {
            "rectangle_px": [x0, y0, x1, y1],
            "x_range_px": [x0, x1],
            "panel_size_px": [width, height],
            "ink_present": False,
            "pass": False,
            "failures": ["all_background"],
        }
    local_bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    bbox_width = local_bbox[2] - local_bbox[0]
    bbox_height = local_bbox[3] - local_bbox[1]
    occupancy_bbox = bbox_width * bbox_height / (width * height)
    occupancy_pixels = int(ink.sum()) / (width * height)
    pads = {
        "left": local_bbox[0],
        "top": local_bbox[1],
        "right": width - local_bbox[2],
        "bottom": height - local_bbox[3],
    }
    failures = []
    if occupancy_bbox < min_occupancy:
        failures.append("bbox_occupancy_below_minimum")
    if occupancy_bbox > max_occupancy:
        failures.append("bbox_occupancy_above_maximum")
    for side, value in pads.items():
        if value < min_pad:
            failures.append(f"{side}_pad_below_minimum")
    return {
        "rectangle_px": [x0, y0, x1, y1],
        "x_range_px": [x0, x1],
        "panel_size_px": [width, height],
        "ink_present": True,
        "ink_bbox_local_px": local_bbox,
        "ink_bbox_global_px": [
            x0 + local_bbox[0],
            y0 + local_bbox[1],
            x0 + local_bbox[2],
            y0 + local_bbox[3],
        ],
        "bbox_occupancy_fraction": occupancy_bbox,
        "ink_pixel_fraction": occupancy_pixels,
        "safety_pad_px": pads,
        "pass": not failures,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("png", type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--panels", type=int, help="Number of equal-width panels")
    group.add_argument(
        "--grid",
        nargs=2,
        type=int,
        metavar=("ROWS", "COLUMNS"),
        help="Equal row-by-column grid measured cell by cell",
    )
    group.add_argument(
        "--boundaries",
        help="Comma-separated pixel boundaries, including 0 and image width",
    )
    group.add_argument(
        "--rectangles",
        help='JSON list of panel rectangles: [[x0,y0,x1,y1], ...]',
    )
    parser.add_argument(
        "--background", default="255,255,255", help="RGB background, default white"
    )
    parser.add_argument("--background-tolerance", type=int, default=10)
    parser.add_argument("--min-occupancy", type=float, default=0.70)
    parser.add_argument("--max-occupancy", type=float, default=0.95)
    parser.add_argument("--min-pad", type=int, default=24)
    parser.add_argument("--max-occupancy-ratio", type=float, default=1.35)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-fail", action="store_true", help="Always exit zero after reporting"
    )
    args = parser.parse_args()

    image = Image.open(args.png).convert("RGB")
    rgb = np.asarray(image)
    background = np.asarray(
        [int(value) for value in args.background.split(",")], dtype=np.uint8
    )
    if background.shape != (3,):
        raise SystemExit(
            "--background must contain exactly three comma-separated integers"
        )
    boundaries = None
    if args.panels is not None:
        boundaries = equal_boundaries(image.width, args.panels)
        rectangles = [
            [x0, 0, x1, image.height]
            for x0, x1 in pairwise(boundaries)
        ]
    elif args.boundaries is not None:
        boundaries = parse_boundaries(args.boundaries, image.width)
        rectangles = [
            [x0, 0, x1, image.height]
            for x0, x1 in pairwise(boundaries)
        ]
    elif args.grid is not None:
        rectangles = equal_grid_rectangles(
            image.width, image.height, args.grid[0], args.grid[1]
        )
    else:
        rectangles = parse_rectangles(args.rectangles, image.width, image.height)
    panels = [
        measure_rectangle(
            rgb,
            x0,
            y0,
            x1,
            y1,
            background=background,
            tolerance=args.background_tolerance,
            min_occupancy=args.min_occupancy,
            max_occupancy=args.max_occupancy,
            min_pad=args.min_pad,
        )
        for x0, y0, x1, y1 in rectangles
    ]
    occupancies = [
        panel.get("bbox_occupancy_fraction")
        for panel in panels
        if panel.get("ink_present")
    ]
    occupancy_ratio = (
        max(occupancies) / min(occupancies)
        if occupancies and min(occupancies) > 0
        else None
    )
    failures = []
    if any(not panel["pass"] for panel in panels):
        failures.append("panel_threshold_failure")
    if occupancy_ratio is not None and occupancy_ratio > args.max_occupancy_ratio:
        failures.append("cross_panel_occupancy_ratio_above_maximum")
    result = {
        "schema_version": 1,
        "png": str(args.png.resolve()),
        "image_size_px": [image.width, image.height],
        "panel_boundaries_px": boundaries,
        "panel_rectangles_px": rectangles,
        "background_rgb": background.tolist(),
        "thresholds": {
            "background_tolerance": args.background_tolerance,
            "min_bbox_occupancy": args.min_occupancy,
            "max_bbox_occupancy": args.max_occupancy,
            "min_safety_pad_px": args.min_pad,
            "max_cross_panel_occupancy_ratio": args.max_occupancy_ratio,
        },
        "panels": panels,
        "cross_panel_bbox_occupancy_ratio": occupancy_ratio,
        "pass": not failures,
        "failures": failures,
    }
    text = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")
    if failures and not args.no_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
