"""Render the README showcase images.

Run from the repository root:

    python docs/build_images.py

Produces ``docs/images/*.png`` from the bundled DAP-4 example. These are the
images embedded in ``README.md`` and are committed to the repository so the
Markdown renders without requiring a build step.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from crystal_viewer.static_publication import plot_crystal as pc  # noqa: E402
from crystal_viewer.static_publication.crystal_scene import build_structure_scene  # noqa: E402
from crystal_viewer.loader import build_bundle_scene, build_loaded_crystal  # noqa: E402
from crystal_viewer.renderer import build_figure, topology_histogram_figure  # noqa: E402
from crystal_viewer.scene import scene_style  # noqa: E402
from crystal_viewer.topology import analyze_topology  # noqa: E402


CIF = REPO_ROOT / "scripts" / "data" / "DAP-4.cif"
IMG_DIR = REPO_ROOT / "docs" / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)


def _save_plotly(fig, path: Path, *, width: int, height: int, scale: int = 2) -> Path:
    fig.write_image(str(path), width=width, height=height, scale=scale)
    print(f"  -> {path.relative_to(REPO_ROOT)}  ({path.stat().st_size // 1024} KB)")
    return path


def _bundle():
    return build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4")


def render_unit_cell(out: Path) -> Path:
    bundle = _bundle()
    cell_scene = build_bundle_scene(bundle, display_mode="unit_cell")
    style = scene_style(
        cell_scene,
        {
            "show_unit_cell": True,
            "show_axes": False,
            "show_labels": False,
            "atom_scale": 0.85,
            "bond_radius": 0.13,
        },
    )
    fig = build_figure(cell_scene, style)
    fig.update_layout(
        title=dict(text=f"DAP-4 unit cell ({len(cell_scene['draw_atoms'])} atoms)", x=0.5),
        margin=dict(l=0, r=0, t=42, b=0),
    )
    return _save_plotly(fig, out, width=900, height=720)


def render_coordination(out: Path, hist_out: Path):
    bundle = _bundle()
    a_target = next(f for f in bundle.topology_fragment_table if f["type"] == "A")
    topology = analyze_topology(bundle, center_index=a_target["index"], cutoff=8.0)
    style = scene_style(
        bundle.scene,
        {
            "show_axes": False,
            "show_labels": False,
            "atom_scale": 1.0,
            "bond_radius": 0.16,
            "topology_enabled": True,
        },
    )
    fig = build_figure(bundle.scene, style, topology_data=topology)
    fig.update_layout(
        title=dict(
            text=f"A-site coordination shell - CN={topology['coordination_number']}",
            x=0.5,
        ),
        margin=dict(l=0, r=0, t=42, b=0),
    )
    _save_plotly(fig, out, width=900, height=720)

    hist = topology_histogram_figure(topology)
    hist.update_layout(
        title=dict(text=f"{a_target['label']} -> X distance histogram (DAP-4)", x=0.5),
        margin=dict(l=40, r=20, t=42, b=42),
    )
    _save_plotly(hist, hist_out, width=900, height=380)


def render_three_modes(out: Path) -> Path:
    bundle = _bundle()
    a_target = next(f for f in bundle.topology_fragment_table if f["type"] == "A")
    topology = analyze_topology(bundle, center_index=a_target["index"], cutoff=8.0)

    formula_scene = build_bundle_scene(bundle, display_mode="formula_unit")
    cell_scene = build_bundle_scene(bundle, display_mode="unit_cell")

    panels = [
        ("Formula unit", formula_scene, {"show_unit_cell": True}, None),
        (
            "Unit cell",
            cell_scene,
            {"show_unit_cell": True, "atom_scale": 0.85, "bond_radius": 0.13},
            None,
        ),
        (
            f"Coordination shell (CN={topology['coordination_number']})",
            bundle.scene,
            {"topology_enabled": True, "show_unit_cell": False},
            topology,
        ),
    ]

    fig = make_subplots(
        rows=1,
        cols=3,
        specs=[[{"type": "scene"}] * 3],
        subplot_titles=[panel[0] for panel in panels],
        horizontal_spacing=0.02,
    )

    for col, (_, scene, style_overrides, topology_data) in enumerate(panels, start=1):
        style = scene_style(scene, {"show_axes": False, "show_labels": False, **style_overrides})
        sub_fig = build_figure(scene, style, topology_data=topology_data)
        for trace in sub_fig.data:
            fig.add_trace(trace, row=1, col=col)
        scene_key = "scene" if col == 1 else f"scene{col}"
        fig.layout[scene_key].update(sub_fig.layout.scene.to_plotly_json())

    fig.update_layout(
        title=dict(text="DAP-4: three display modes", x=0.5),
        paper_bgcolor="white",
        margin=dict(l=10, r=10, t=70, b=10),
        showlegend=False,
    )
    return _save_plotly(fig, out, width=1500, height=560)


def render_publication(out: Path) -> Path:
    ops = pc._scene_ops()
    scene = build_structure_scene(
        ops,
        name="DAP-4",
        cif_path=str(CIF),
        title=r"DAP-4  (P1, $a$=14.43 Å)",
    )
    fig = plt.figure(figsize=(6.5, 6.0), dpi=240)
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
        "ORTEP-style export via crystal_viewer.static_publication.plot_crystal",
        fontsize=10,
        y=0.97,
    )
    fig.savefig(str(out), bbox_inches="tight", facecolor="white", dpi=240)
    plt.close(fig)
    print(f"  -> {out.relative_to(REPO_ROOT)}  ({out.stat().st_size // 1024} KB)")
    return out


def render_banner(images: Iterable[Path], out: Path) -> Path:
    images = list(images)
    fig, axes = plt.subplots(1, len(images), figsize=(4.4 * len(images), 4.4), dpi=180)
    if len(images) == 1:
        axes = [axes]
    for ax, image_path in zip(axes, images):
        ax.imshow(mpimg.imread(image_path))
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
    fig.subplots_adjust(left=0.005, right=0.995, top=0.995, bottom=0.005, wspace=0.01)
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  -> {out.relative_to(REPO_ROOT)}  ({out.stat().st_size // 1024} KB)")
    return out


def main() -> None:
    print("Rendering README showcase images...")
    cell = render_unit_cell(IMG_DIR / "feature_unit_cell.png")
    render_coordination(IMG_DIR / "feature_coordination.png", IMG_DIR / "feature_histogram.png")
    panel = render_three_modes(IMG_DIR / "feature_three_modes.png")
    pub = render_publication(IMG_DIR / "feature_publication.png")
    render_banner([cell, IMG_DIR / "feature_coordination.png", pub], IMG_DIR / "banner.png")
    print("Done.")


if __name__ == "__main__":
    main()
