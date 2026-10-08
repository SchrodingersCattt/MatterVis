"""Stitch the three README panels into ``feature_combined.png``.

The three panels occupy equal thirds. ``render_panels.py`` draws them:
disorder fades atoms and bonds by occupancy, DAP-7 is viewed straight down
the b axis with one A hull and one B hull kept visible, and PETN carries a
fruit-green mock arrow on each terminal oxygen, perpendicular to the N–O bond.

    PYTHONPATH=. python docs/images/render_panels.py

    mat-vis render docs/images/showcase_petn_molecule.xyz \\
      -o docs/images/panel_mode.png --backend cpu \\
      --orthogonal --background '#FFFFFF' --style ball_stick --show-hydrogen \\
      --view-direction 0.35 0.55 0.76 \\
      --vector-overlays docs/images/showcase_petn_vectors.json \\
      --atom-scale 0.72 --bond-radius 0.10 \\
      --camera-distance 1.35 --framing-margin 1.08 \\
      --width 800 --height 800 --scale 2

The PETN arrows are a mock displacement perpendicular to the N–O bonds, not a phonon.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
PANELS = (
    ("panel_disorder.png", "Disorder", "all"),
    ("panel_polyhedron.png", "Polyhedron", "largest"),
    ("panel_mode.png", "Mode", "all"),
)


def _crop_all(image: Image.Image, pad: int = 36) -> Image.Image:
    rgb = image.convert("RGB")
    pixels = rgb.load()
    width, height = rgb.size
    min_x, min_y, max_x, max_y = width, height, 0, 0
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            red, green, blue = pixels[x, y]
            if red < 248 or green < 248 or blue < 248:
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
    return rgb.crop(
        (
            max(0, min_x - pad),
            max(0, min_y - pad),
            min(width, max_x + pad + 1),
            min(height, max_y + pad + 1),
        )
    )


def _crop_largest(image: Image.Image, pad: int = 32) -> Image.Image:
    """Keep the largest ink island so a detached axis compass is not included."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    step = 3
    mask_w = (width + step - 1) // step
    mask_h = (height + step - 1) // step
    ink = [[False] * mask_w for _ in range(mask_h)]
    for y in range(height):
        for x in range(width):
            red, green, blue = pixels[x, y]
            if red < 248 or green < 248 or blue < 248:
                ink[y // step][x // step] = True
    seen = [[False] * mask_w for _ in range(mask_h)]
    best: list[tuple[int, int]] = []
    for y in range(mask_h):
        for x in range(mask_w):
            if not ink[y][x] or seen[y][x]:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            seen[y][x] = True
            cells = [(x, y)]
            while queue:
                cx, cy = queue.popleft()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (
                        0 <= nx < mask_w
                        and 0 <= ny < mask_h
                        and ink[ny][nx]
                        and not seen[ny][nx]
                    ):
                        seen[ny][nx] = True
                        queue.append((nx, ny))
                        cells.append((nx, ny))
            if len(cells) > len(best):
                best = cells
    xs = [cell[0] for cell in best]
    ys = [cell[1] for cell in best]
    return rgb.crop(
        (
            max(0, min(xs) * step - pad),
            max(0, min(ys) * step - pad),
            min(width, (max(xs) + 1) * step + pad),
            min(height, (max(ys) + 1) * step + pad),
        )
    )


def main() -> None:
    slot = 720
    label_h = 56
    canvas = Image.new("RGB", (slot * len(PANELS), slot + label_h), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("arial.ttf", 32)
    for index, (filename, label, mode) in enumerate(PANELS):
        image = Image.open(ROOT / filename)
        cropped = _crop_largest(image) if mode == "largest" else _crop_all(image)
        scale = min(slot / cropped.width, slot / cropped.height)
        fitted = cropped.resize(
            (max(1, int(round(cropped.width * scale))), max(1, int(round(cropped.height * scale)))),
            Image.Resampling.LANCZOS,
        )
        origin_x = index * slot + (slot - fitted.width) // 2
        origin_y = (slot - fitted.height) // 2
        canvas.paste(fitted, (origin_x, origin_y))
        text_box = draw.textbbox((0, 0), label, font=font)
        text_w = text_box[2] - text_box[0]
        draw.text(
            (index * slot + (slot - text_w) / 2, slot + 10),
            label,
            fill="#333333",
            font=font,
        )
    canvas.save(ROOT / "feature_combined.png", "PNG")


if __name__ == "__main__":
    main()
