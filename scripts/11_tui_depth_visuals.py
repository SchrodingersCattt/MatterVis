"""Capture fixed 160x48 headless Textual frames for issue #211.

Run this script against each checkout using PYTHONPATH, with --output pointing
to separate before/after directories. PNG conversion requires cairosvg; SVG and
plain canvas text are always saved. Sources are repository fixtures, with no
external data downloads.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from mat_viewer.math.camera import Camera
from mat_viewer.tui.app import CrystalTUI
from mat_viewer.tui.loader_adapter import load_for_tui

ROOT = Path(__file__).resolve().parents[1]
SIZE = (160, 48)


async def capture(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, source in (
        ("dap4", ROOT / "scripts/data/DAP-4.cif"),
        ("caf2", ROOT / "tests/fixtures/cod_1000043.cif"),
    ):
        crystal = load_for_tui(str(source))
        camera = Camera.from_view_name("diagonal", crystal)
        for level in ("atom", "molecule"):
            app = CrystalTUI(
                crystal,
                camera=camera,
                initial_level=level,
                label_mode="element",
                mono=True,
            )
            async with app.run_test(size=SIZE) as pilot:
                await pilot.pause()
                await pilot.pause()
                stem = f"{name}-{level}"
                canvas = app.query_one("#canvas")
                (output / f"{stem}.txt").write_text(
                    canvas.frame_text + "\n", encoding="utf-8"
                )
                svg_path = app.save_screenshot(filename=f"{stem}.svg", path=str(output))
                try:
                    import cairosvg
                except ImportError:
                    continue
                svg = Path(svg_path).read_text(encoding="utf-8")
                # Cairo does not reliably select the CSS fallback font list.
                svg = svg.replace(
                    "font-family: Fira Code, monospace;",
                    'font-family: "DejaVu Sans Mono";',
                )
                cairosvg.svg2png(
                    bytestring=svg.encode(), write_to=str(output / f"{stem}.png")
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "verification_screens/tui_depth/after"
    )
    asyncio.run(capture(parser.parse_args().output.resolve()))
