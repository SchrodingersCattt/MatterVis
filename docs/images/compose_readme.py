"""Stitch the three README panels into ``feature_combined.png``.

Independent fit: the panels are not on one physical scale. Render them first:

    mat-vis render docs/images/showcase_caffeine_pair.cif \\
      -o docs/images/panel_disorder.png --backend cpu \\
      --orthogonal --background '#FFFFFF' --style ball_stick \\
      --no-hydrogen --no-cell \\
      --view-direction -0.1619 0.6070 0.7780 \\
      --camera-up 0.9799 0.0055 0.1992 \\
      --atom-scale 0.72 --bond-radius 0.09 \\
      --camera-distance 1.25 --framing-margin 1.06 \\
      --width 800 --height 800 --scale 2

    mat-vis render docs/images/showcase_dap7.cif \\
      -o docs/images/panel_polyhedron.png --backend cpu \\
      --orthogonal --background '#FFFFFF' \\
      --view unit_cell --style ball_stick \\
      --show-cell --show-axes --no-hydrogen --no-boundary-replicas \\
      --polyhedron '{"center":"Cl","ligand":"O","level":"atom"}' \\
      --atom-scale 0.82 --bond-radius 0.08 \\
      --width 800 --height 800 --scale 2

    mat-vis render docs/images/showcase_petn_molecule.xyz \\
      -o docs/images/panel_mode.png --backend cpu \\
      --orthogonal --background '#FFFFFF' --style ball_stick \\
      --view-direction 0.35 0.55 0.76 \\
      --vector-overlays docs/images/showcase_petn_vectors.json \\
      --atom-scale 0.72 --bond-radius 0.10 \\
      --camera-distance 1.35 --framing-margin 1.08 \\
      --width 800 --height 800 --scale 2

The PETN arrows are a mock nitrate stretch, not a phonon.
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
    target_h = 640
    fitted: list[tuple[Image.Image, str]] = []
    for filename, label, mode in PANELS:
        image = Image.open(ROOT / filename)
        cropped = _crop_largest(image) if mode == "largest" else _crop_all(image)
        width = max(1, int(round(cropped.width * target_h / cropped.height)))
        fitted.append(
            (cropped.resize((width, target_h), Image.Resampling.LANCZOS), label)
        )
    gap = 40
    label_h = 56
    total_w = sum(image.width for image, _ in fitted) + gap * (len(fitted) - 1)
    canvas = Image.new("RGB", (total_w, target_h + label_h), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("arial.ttf", 32)
    x = 0
    for image, label in fitted:
        canvas.paste(image, (x, 0))
        text_box = draw.textbbox((0, 0), label, font=font)
        text_w = text_box[2] - text_box[0]
        draw.text(
            (x + (image.width - text_w) / 2, target_h + 10),
            label,
            fill="#333333",
            font=font,
        )
        x += image.width + gap
    canvas.save(ROOT / "feature_combined.png", "PNG")


if __name__ == "__main__":
    main()
