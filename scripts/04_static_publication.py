"""Render an ORTEP-style publication panel via the vendored matplotlib renderer.

Run from the repository root:

    python scripts/04_static_publication.py

Uses ``crystal_viewer.static_publication.plot_crystal``  -  the same code that powers
``POST /api/v1/export``  -  to draw a high-DPI Axes3D figure that prints well
in greyscale.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from crystal_viewer.static_publication import plot_crystal as pc  # noqa: E402
from crystal_viewer.static_publication.crystal_scene import build_structure_scene  # noqa: E402


HERE = Path(__file__).resolve().parent
CIF = HERE / "data" / "DAP-4.cif"
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    ops = pc._scene_ops()
    scene = build_structure_scene(
        ops,
        name="DAP-4",
        cif_path=str(CIF),
        title=r"DAP-4  (P1, $a$=14.43 Å)",
    )

    fig = plt.figure(figsize=(7.0, 6.5), dpi=300)
    ax = fig.add_subplot(111, projection="3d")

    pc.draw_scene(ax, scene)
    fig.canvas.draw()
    pc.add_axes_overlay(
        ax,
        scene["R"],
        scene["M"],
        scene["draw_atoms"],
        scene["view_x"],
        scene["view_y"],
    )
    pc.draw_labels_2d(
        ax,
        [
            (item["atom_cart"], item["label_cart"], item["text"], item["is_minor"])
            for item in scene["label_items"]
        ],
        scene["view_x"],
        scene["view_y"],
    )
    fig.suptitle(
        "DAP-4 ordered polymorph - one formula unit\n"
        "ORTEP-style ellipsoids - H atoms hidden",
        fontsize=10,
        y=0.97,
    )

    for ext in ("png", "pdf"):
        out = OUTPUT_DIR / f"04_static_publication.{ext}"
        kw = dict(bbox_inches="tight", facecolor="white")
        if ext == "png":
            kw["dpi"] = 300
        fig.savefig(str(out), **kw)
        print(f"Wrote {out}")
    plt.close(fig)


if __name__ == "__main__":
    main()
