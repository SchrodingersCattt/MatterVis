"""Create a flat publication-style ORTEP figure."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mat_viewer.loader import build_loaded_crystal, build_bundle_scene  # noqa: E402
from mat_viewer.renderer import build_figure  # noqa: E402
from mat_viewer.scene import scene_style  # noqa: E402


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(HERE / "data" / "DAP-4.cif"), title="DAP-4 flat ORTEP")
    scene = build_bundle_scene(bundle, display_mode="formula_unit")
    style = scene_style(
        scene,
        {
            "material": "flat",
            "style": "ortep",
            "disorder": "outline_rings",
            "ortep_octant_shading": True,
            "show_labels": False,
            "show_axes": False,
        },
    )
    fig = build_figure(scene, style)
    fig.write_image(str(OUTPUT_DIR / "09_ortep_publication.pdf"), width=800, height=700, scale=2)
    fig.write_image(str(OUTPUT_DIR / "09_ortep_publication.png"), width=800, height=700, scale=2)


if __name__ == "__main__":
    main()
