"""Render an ORTEP-III publication figure using the unified render() API.

Run from the repository root:

    python scripts/04_static_publication.py

Produces a classic 2D ORTEP figure (Matplotlib vector output) suitable for
IUCr/Acta Cryst submission: black ellipse outlines, octant hatching, thin
black bond lines, atom labels, white background.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from crystal_viewer.loader import build_loaded_crystal, build_bundle_scene  # noqa: E402
from crystal_viewer.renderer import render  # noqa: E402

HERE = Path(__file__).resolve().parent
CIF = HERE / "data" / "DAP-4.cif"
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4")
    scene = build_bundle_scene(bundle, display_mode="formula_unit")

    result = render(scene, {
        "material": "flat",
        "style": "ortep",
        "ortep_probability": 0.5,
        "show_hydrogen": False,
        "show_labels": True,
        "bond_radius": 0.12,
    })

    result.save(str(OUTPUT_DIR / "04_static_publication.pdf"))
    result.save(str(OUTPUT_DIR / "04_static_publication.png"), dpi=300)
    print(f"✓ ORTEP publication → {OUTPUT_DIR / '04_static_publication.pdf'}")


if __name__ == "__main__":
    main()

