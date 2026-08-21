"""Render a single CIF to a static PNG and an interactive HTML page.

Run from the repository root:

    python scripts/01_quick_render.py

Outputs are written next to the script under ``scripts/_outputs/``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mat_viewer.loader import build_loaded_crystal  # noqa: E402
from mat_viewer.renderer import build_figure  # noqa: E402
from mat_viewer.scene import scene_style  # noqa: E402


HERE = Path(__file__).resolve().parent
CIF = HERE / "data" / "DAP-4.cif"
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4 · unit cell")

    from mat_viewer.loader import build_bundle_scene
    cell_scene = build_bundle_scene(bundle, display_mode="unit_cell")

    style = scene_style(
        cell_scene,
        {
            "show_unit_cell": True,
            "show_axes": False,
            "show_labels": False,
            "atom_scale": 0.9,
            "bond_radius": 0.14,
        },
    )

    fig = build_figure(cell_scene, style)
    fig.update_layout(title=dict(text=f"{bundle.title}  ·  {len(cell_scene['draw_atoms'])} atoms drawn", x=0.5))

    png = OUTPUT_DIR / "01_quick_render.png"
    html = OUTPUT_DIR / "01_quick_render.html"
    fig.write_image(str(png), width=900, height=720, scale=2)
    fig.write_html(str(html), include_plotlyjs="cdn", full_html=True)

    print(f"Wrote PNG  : {png}  ({os.path.getsize(png)} bytes)")
    print(f"Wrote HTML : {html} (open it in a browser to orbit the structure)")


if __name__ == "__main__":
    main()
