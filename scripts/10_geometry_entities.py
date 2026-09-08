"""Render a crystal together with a real 3-D geometry entity.

Run from the repository root::

    python scripts/10_geometry_entities.py

The output is an interactive HTML figure under ``scripts/_outputs``.  The
open cylinder is sent through the same Plotly ``Mesh3d`` depth-tested path as
the atoms and bonds; replace it with ``mesh_entity(vertices, faces, ...)`` for
an arbitrary solid or surface.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mat_viewer.loader import build_bundle_scene, build_loaded_crystal  # noqa: E402
from mat_viewer.presets import DEFAULT_STYLE  # noqa: E402
from mat_viewer.renderer import render, through_cylinder_entity  # noqa: E402


HERE = Path(__file__).resolve().parent
CIF = HERE / "data" / "DAP-4.cif"
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4")
    scene = build_bundle_scene(bundle, display_mode="unit_cell")
    scene["geometry_entities"] = [
        through_cylinder_entity(
            lattice=bundle.M,
            direction_hkl=[1, 0, 0],
            radius=2.5,
            center_frac=[0.5, 0.5, 0.5],
            caps=False,
            segments=32,
            name="through-cylinder",
            entity_id="demo-cylinder-100",
            color="#4F7CFF",
            opacity=1.0,
            show_edges=True,
        )
    ]

    style = {
        **DEFAULT_STYLE,
        "material": "mesh",
        "style": "ball_stick",
        "display_mode": "unit_cell",
        "projection": "orthographic",
        "show_axes": False,
        "show_labels": False,
        "show_unit_cell": True,
        "show_title": False,
    }
    figure = render(scene, style).to_plotly()
    output = OUTPUT_DIR / "10_geometry_entities.html"
    figure.write_html(str(output), include_plotlyjs="cdn", full_html=True)
    print(f"✓ 3-D geometry entity demo → {output}")


if __name__ == "__main__":
    main()
