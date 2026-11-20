"""Save the painted Fit frame when a margin assertion fails.

The 8% margin and the 8px centering check stay where they are. This module
only records the screenshot those checks already measured, so a later look
can tell structure, axes, controls, and a shifted plot box apart.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw

_INK_CEILING = 250
_MARGIN = 0.08
_IDENTITY = (
    "projection",
    "eye",
    "center",
    "aspect",
    "ranges",
    "revision",
    "renderRevision",
    "sceneId",
    "sceneGeneration",
    "geometryVersion",
    "coordinateDigest",
    "status",
    "boundaryChecked",
    "plot",
    "canvas",
    "chrome",
    "overlays",
)


def covered(x: int, y: int, chrome: list[Mapping[str, Any]]) -> bool:
    """True when a pixel sits in a control rectangle, including its 4px pad."""

    for item in chrome:
        if (
            item["x"] - 4 <= x <= item["x"] + item["width"] + 4
            and item["y"] - 4 <= y <= item["y"] + item["height"] + 4
        ):
            return True
    return False


def ink_box(
    path: Path,
    plot: Mapping[str, Any],
    chrome: list[Mapping[str, Any]],
) -> tuple[int, int, int, int, dict[str, int]]:
    """Bounding box of counted ink inside the plot, and the plot rectangle."""

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        pixels = image.load()
        left = max(0, int(plot["x"]))
        top = max(0, int(plot["y"]))
        right = min(image.width, int(plot["x"] + plot["width"]))
        bottom = min(image.height, int(plot["y"] + plot["height"]))
        visible = {"x": left, "y": top, "width": right - left, "height": bottom - top}
        xs: list[int] = []
        ys: list[int] = []
        for y in range(top, bottom):
            for x in range(left, right):
                if covered(x, y, chrome):
                    continue
                red, green, blue = pixels[x, y]
                if max(red, green, blue) < _INK_CEILING:
                    xs.append(x)
                    ys.append(y)
    if not xs:
        return None, None, None, None, visible
    return min(xs), min(ys), max(xs), max(ys), visible


def _rect_distance(x: int, y: int, item: Mapping[str, Any]) -> float:
    left = float(item["x"])
    top = float(item["y"])
    right = left + float(item["width"])
    bottom = top + float(item["height"])
    dx = 0.0 if left <= x <= right else min(abs(x - left), abs(x - right))
    dy = 0.0 if top <= y <= bottom else min(abs(y - top), abs(y - bottom))
    return (dx * dx + dy * dy) ** 0.5


def _nearest_chrome(x: int, y: int, chrome: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not chrome:
        return None
    chosen = min(chrome, key=lambda item: _rect_distance(x, y, item))
    return {
        "id": chosen.get("id"),
        "distance_px": round(_rect_distance(x, y, chosen), 2),
    }


def _sample(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {key: value.get(key) for key in _IDENTITY}


def measure_gap(shot: Mapping[str, Any], shots_dir: Path) -> dict[str, Any]:
    """One frame's gaps, using the same ink rule as the painted assertion."""

    chrome = list(shot.get("chrome") or [])
    path = shots_dir / str(shot["file"])
    x0, y0, x1, y1, visible = ink_box(path, shot["plot"], chrome)
    if x0 is None:
        with Image.open(path) as opened:
            image_size = [opened.width, opened.height]
        return {
            "file": shot["file"],
            "path": path,
            "failed": True,
            "empty_ink": True,
            "gaps": None,
            "pad": None,
            "inside": False,
            "tight": False,
            "centered": False,
            "spans": None,
            "aspect": shot.get("aspect"),
            "ink_bbox": None,
            "visible": visible,
            "image_size": image_size,
            "problem": (shot["file"], "no counted ink", visible),
        }
    short = min(visible["width"], visible["height"])
    pad = _MARGIN * short
    gaps = (
        x0 - visible["x"],
        visible["x"] + visible["width"] - x1,
        y0 - visible["y"],
        visible["y"] + visible["height"] - y1,
    )
    inside = (
        x0 >= visible["x"] + pad - 2
        and y0 >= visible["y"] + pad - 2
        and x1 <= visible["x"] + visible["width"] - pad + 2
        and y1 <= visible["y"] + visible["height"] - pad + 2
    )
    tight = min(gaps) <= pad + 0.04 * short
    centered = abs(gaps[0] - gaps[1]) <= 8 and abs(gaps[2] - gaps[3]) <= 8
    spans = None
    ranges = shot.get("ranges")
    if isinstance(ranges, Mapping):
        spans = [
            round(abs(ranges[axis][1] - ranges[axis][0]), 2) for axis in ("x", "y", "z")
        ]
    with Image.open(path) as opened:
        image_size = [opened.width, opened.height]
    return {
        "file": shot["file"],
        "path": path,
        "failed": not inside or not tight or not centered,
        "gaps": gaps,
        "pad": pad,
        "inside": inside,
        "tight": tight,
        "centered": centered,
        "spans": spans,
        "aspect": shot.get("aspect"),
        "ink_bbox": [x0, y0, x1, y1],
        "visible": visible,
        "image_size": image_size,
        "empty_ink": False,
        "problem": (
            shot["file"],
            [round(gap, 1) for gap in gaps],
            round(pad, 1),
            inside,
            tight,
            centered,
            spans,
            shot.get("aspect"),
        ),
    }


def _bottom_pixels(
    image: Image.Image,
    record: Mapping[str, Any],
    chrome: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    pixels = image.convert("RGB").load()
    visible = record["visible"]
    _x0, _y0, _x1, y1 = record["ink_bbox"]
    found: list[dict[str, Any]] = []
    left = int(visible["x"])
    right = left + int(visible["width"])
    for y in range(max(int(visible["y"]), y1 - 3), y1 + 1):
        for x in range(left, right):
            if covered(x, y, chrome):
                continue
            red, green, blue = pixels[x, y]
            if max(red, green, blue) >= _INK_CEILING:
                continue
            found.append(
                {
                    "x": x,
                    "y": y,
                    "rgb": [red, green, blue],
                    "chrome": _nearest_chrome(x, y, chrome),
                }
            )
    if len(found) <= 64:
        return found
    step = max(1, len(found) // 64)
    return found[::step][:64]


def _outline(draw: ImageDraw.ImageDraw, rect: Mapping[str, Any], color: tuple[int, int, int]) -> None:
    if not rect or rect.get("width") in (None, 0) or rect.get("height") in (None, 0):
        return
    draw.rectangle(
        (
            float(rect["x"]),
            float(rect["y"]),
            float(rect["x"]) + float(rect["width"]) - 1,
            float(rect["y"]) + float(rect["height"]) - 1,
        ),
        outline=color,
    )


def _write_mask(
    image: Image.Image,
    record: Mapping[str, Any],
    chrome: list[Mapping[str, Any]],
    destination: Path,
    shot: Mapping[str, Any],
) -> None:
    mask = Image.new("RGB", image.size, (0, 0, 0))
    target = mask.load()
    source = image.convert("RGB").load()
    visible = record["visible"]
    left = int(visible["x"])
    top = int(visible["y"])
    right = left + int(visible["width"])
    bottom = top + int(visible["height"])
    for y in range(top, bottom):
        for x in range(left, right):
            red, green, blue = source[x, y]
            if max(red, green, blue) >= _INK_CEILING:
                continue
            target[x, y] = (255, 140, 0) if covered(x, y, chrome) else (255, 0, 255)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((left, top, right - 1, bottom - 1), outline=(0, 255, 255))
    if record.get("pad") is not None:
        pad = float(record["pad"])
        draw.rectangle(
            (
                left + pad,
                top + pad,
                right - 1 - pad,
                bottom - 1 - pad,
            ),
            outline=(0, 255, 0),
        )
    canvas = shot.get("canvas")
    if isinstance(canvas, Mapping):
        _outline(draw, canvas, (255, 255, 255))
    for item in chrome:
        _outline(draw, item, (255, 140, 0))
    overlays = shot.get("overlays") if isinstance(shot.get("overlays"), Mapping) else {}
    for key in ("compass", "status"):
        node = overlays.get(key) if isinstance(overlays, Mapping) else None
        if isinstance(node, Mapping):
            _outline(draw, node, (80, 160, 255))
    if record.get("ink_bbox"):
        x0, y0, x1, y1 = record["ink_bbox"]
        draw.rectangle((x0, y0, x1, y1), outline=(255, 255, 0))
    mask.save(destination)


def write_gap_artifacts(
    directory: Path,
    shots: list[Mapping[str, Any]],
    shots_dir: Path,
    records: list[Mapping[str, Any]],
) -> Path:
    """Copy each failing frame's PNG, mask, bottom crop, and camera record."""

    directory.mkdir(parents=True, exist_ok=True)
    by_file = {str(shot.get("file")): shot for shot in shots}
    index: list[dict[str, Any]] = []
    for record in records:
        if not record.get("failed"):
            continue
        shot = by_file.get(str(record["file"]), {})
        chrome = list(shot.get("chrome") or [])
        source = Path(record["path"])
        stem = Path(str(record["file"])).stem
        shutil.copyfile(source, directory / f"{stem}.png")
        lowest: list[dict[str, Any]] = []
        with Image.open(source) as opened:
            image = opened.convert("RGB")
            _write_mask(image, record, chrome, directory / f"{stem}-mask.png", shot)
            if record.get("ink_bbox"):
                _x0, _y0, _x1, y1 = record["ink_bbox"]
                visible = record["visible"]
                crop = image.crop(
                    (
                        int(visible["x"]),
                        max(0, int(y1) - 47),
                        int(visible["x"]) + int(visible["width"]),
                        min(image.height, int(y1) + 1),
                    )
                )
                crop.save(directory / f"{stem}-bottom.png")
                lowest = _bottom_pixels(image, record, chrome)
        gaps = record.get("gaps")
        payload = {
            "legend": {
                "mask_magenta": "counted ink (not white, outside control rectangles)",
                "mask_orange_pixel": "dark pixel inside a control rectangle, not counted",
                "mask_cyan": "plot rectangle the gaps are measured against",
                "mask_green": "8% inset of the shorter plot side",
                "mask_white": "canvas rectangle, when the shot recorded one",
                "mask_orange_outline": "control rectangle without the 4px pad",
                "mask_blue": "compass or render-status rectangle, recorded and not excluded",
                "mask_yellow": "counted ink bounding box",
                "original": f"{stem}.png",
                "bottom": f"{stem}-bottom.png is the original pixels along the lowest counted ink",
            },
            "file": record["file"],
            "image_size": record["image_size"],
            "empty_ink": bool(record.get("empty_ink")),
            "gaps_left_right_top_bottom": None if gaps is None else [round(gap, 4) for gap in gaps],
            "pad": record["pad"],
            "inside": record["inside"],
            "tight": record["tight"],
            "centered": record["centered"],
            "spans": record["spans"],
            "ink_bbox": record["ink_bbox"],
            "visible": record["visible"],
            "after": _sample(shot),
            "before": _sample(shot.get("before")),
            "lowest_counted_ink": lowest,
        }
        (directory / f"{stem}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        index.append({"file": record["file"], "stem": stem})
    (directory / "index.json").write_text(
        json.dumps(
            {
                "legend": "One directory per failing painted Fit margin check. Margins are not changed.",
                "frames": index,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return directory


def preserve_browser_files(
    directory: Path,
    shots_dir: Path,
    *,
    reason: str,
    stdout: str = "",
    stderr: str = "",
) -> Path:
    """Copy screenshots and logs when the runner stops before the margin check.

    Empty ink, a Node failure, a timeout, and a toast still fail. This only
    keeps the files those exits used to delete with the temporary directory.
    """

    directory.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if shots_dir.is_dir():
        for path in sorted(shots_dir.iterdir()):
            if path.suffix.lower() not in {".png", ".json", ".txt"}:
                continue
            shutil.copyfile(path, directory / path.name)
            copied.append(path.name)
    (directory / "stdout.txt").write_text(stdout or "", encoding="utf-8")
    (directory / "stderr.txt").write_text(stderr or "", encoding="utf-8")
    (directory / "reason.json").write_text(
        json.dumps({"reason": reason, "files": copied}, indent=2),
        encoding="utf-8",
    )
    return directory
