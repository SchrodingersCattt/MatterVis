"""Render the README showcase images.

Run from the repository root:

    python docs/build_images.py    (thin wrapper)
    python paper/showcase/build_images.py   (canonical)

Produces ``docs/images/*.png`` from the bundled DAP-4 example. These are the
images embedded in ``README.md`` and are committed to the repository so the
Markdown renders without requiring a build step.

All figures use the Matplotlib flat ORTEP renderer via ``render()``, which
is the only reliable export path on headless/CI/Windows environments.
The showcase keeps MatterVis's default element-colour palette; flat ORTEP
is a publication backend, not a monochrome-only style.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.image as mpimg  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from crystal_viewer.loader import build_bundle_scene, build_loaded_crystal  # noqa: E402
from crystal_viewer.renderer import render  # noqa: E402
from crystal_viewer.scene import scene_style  # noqa: E402
from crystal_viewer.topology import analyze_topology  # noqa: E402


CIF = REPO_ROOT / "scripts" / "data" / "DAP-4.cif"
IMG_DIR = REPO_ROOT / "docs" / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)


def _bundle():
    return build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4")


def _ortep_style(scene, **overrides) -> dict:
    """Build a flat ORTEP style dict — always goes through Matplotlib, never hangs."""
    base = {
        "material": "flat",
        "style": "ortep",
        "show_hydrogen": False,
        "show_labels": False,
        "show_axes": False,
        "show_unit_cell": False,
        "ortep_probability": 0.5,
        "ortep_mode": "ortep_axes",
        "bond_radius": 0.12,
        "atom_scale": 1.0,
    }
    base.update(overrides)
    return scene_style(scene, base)


def _save_render(result, path: Path, *, width: int = 900, height: int = 720, dpi: int = 240) -> Path:
    """Save a render() result (always Matplotlib, never Kaleido)."""
    result.save(str(path), width=width, height=height, dpi=dpi)
    print(f"  -> {path.relative_to(REPO_ROOT)}  ({path.stat().st_size // 1024} KB)")
    return path


# ── renderers ────────────────────────────────────────────────────────────────


def render_unit_cell(out: Path) -> Path:
    """Unit cell — flat ORTEP of the full unit cell."""
    bundle = _bundle()
    cell_scene = build_bundle_scene(bundle, display_mode="unit_cell")
    style = _ortep_style(cell_scene, show_unit_cell=True, atom_scale=0.85, bond_radius=0.13)
    result = render(cell_scene, style)
    return _save_render(result, out)


def render_coordination(out: Path, hist_out: Path):
    """Coordination shell with convex hull overlay + distance histogram."""
    bundle = _bundle()
    a_target = next(f for f in bundle.topology_fragment_table if f["type"] == "A")
    topology = analyze_topology(
        bundle, center_index=a_target["index"], cutoff=8.0,
        ligand_species=("ClO4",),
        center_species="C6N2",
    )
    style = _ortep_style(bundle.scene, atom_scale=1.0, bond_radius=0.16)
    style["topology_enabled"] = True
    result = render(bundle.scene, style, topology_data=topology)
    _save_render(result, out)

    # Distance histogram — Matplotlib bar chart (no Kaleido dependency)
    distances = topology.get("all_distances", [])
    shell_set = set(topology.get("distances", []))
    fig_h, ax_h = plt.subplots(figsize=(9, 3.8), facecolor="white")
    ax_h.set_facecolor("white")
    if distances:
        colors = ["#7C5CBF" if d in shell_set else "#C9C9E8" for d in distances]
        ax_h.bar(range(1, len(distances) + 1), distances, color=colors, width=0.7)
    ax_h.set_xlabel("Neighbor rank")
    ax_h.set_ylabel("Distance (Å)")
    ax_h.set_title(f"{a_target['label']} → X distance histogram (DAP-4)")
    fig_h.tight_layout()
    fig_h.savefig(hist_out, dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig_h)
    print(f"  -> {hist_out.relative_to(REPO_ROOT)}  ({hist_out.stat().st_size // 1024} KB)")


def render_three_modes(out: Path) -> Path:
    """Multi-panel: formula unit, unit cell, coordination shell — stitched in matplotlib."""
    bundle = _bundle()
    a_target = next(f for f in bundle.topology_fragment_table if f["type"] == "A")
    topology = analyze_topology(
        bundle, center_index=a_target["index"], cutoff=8.0,
        ligand_species=("ClO4",),
        center_species="C6N2",
    )

    formula_scene = build_bundle_scene(bundle, display_mode="formula_unit")
    cell_scene = build_bundle_scene(bundle, display_mode="unit_cell")

    # Render each panel as flat ORTEP
    tmp = REPO_ROOT / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    for label, scene, extra_style in [
        ("formula", formula_scene, {"show_unit_cell": True}),
        ("cell", cell_scene, {"show_unit_cell": True, "atom_scale": 0.85, "bond_radius": 0.13}),
        ("coord", bundle.scene, {"topology_enabled": True, "show_unit_cell": False}),
    ]:
        style = _ortep_style(scene, **extra_style)
        kwargs = {"topology_data": topology} if label == "coord" else {}
        result = render(scene, style, **kwargs)
        result.save(str(tmp / f"_panel_{label}.png"), width=500, height=500, dpi=150)

    # Stitch panels
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.6), dpi=100)
    titles = ["Formula unit", "Unit cell", "Coordination shell"]
    for ax, title, label in zip(axes, titles, ["formula", "cell", "coord"]):
        ax.imshow(mpimg.imread(tmp / f"_panel_{label}.png"))
        ax.set_title(title, fontsize=12, pad=4)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
    fig.subplots_adjust(left=0.005, right=0.995, top=0.93, bottom=0.01, wspace=0.02)
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  -> {out.relative_to(REPO_ROOT)}  ({out.stat().st_size // 1024} KB)")
    return out


def render_publication(out: Path) -> Path:
    """ORTEP-style publication-quality Matplotlib export."""
    bundle = _bundle()
    scene = build_bundle_scene(bundle, display_mode="formula_unit")
    style = _ortep_style(scene, show_labels=True, bond_radius=0.12)
    result = render(scene, style)
    return _save_render(result, out, width=900, height=720, dpi=240)


def render_asymmetric_unit(out: Path) -> Path:
    """Asymmetric-unit diagnostic view with the crystallographic cell."""
    bundle = _bundle()
    scene = build_bundle_scene(bundle, display_mode="asymmetric_unit")
    style = _ortep_style(
        scene,
        show_unit_cell=True,
        show_labels=False,
        atom_scale=0.9,
        bond_radius=0.12,
    )
    result = render(scene, style)
    return _save_render(result, out, width=900, height=720, dpi=240)


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


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    print("Rendering README showcase images...")
    cell = render_unit_cell(IMG_DIR / "feature_unit_cell.png")
    render_coordination(IMG_DIR / "feature_coordination.png", IMG_DIR / "feature_histogram.png")
    panel = render_three_modes(IMG_DIR / "feature_three_modes.png")
    pub = render_publication(IMG_DIR / "feature_publication.png")
    asu = render_asymmetric_unit(IMG_DIR / "feature_asymmetric_unit.png")
    render_banner([cell, IMG_DIR / "feature_coordination.png", pub], IMG_DIR / "banner.png")
    print("Done.")


if __name__ == "__main__":
    main()
