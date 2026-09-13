"""Render project-independent implicit surfaces through MatterVis.

Run from the repository root::

    python scripts/11_implicit_geometry.py

The example deliberately has no crystal or project-specific imports. Each
scalar field is sampled into a Cartesian triangle mesh and then rendered by
the same depth-tested ``Mesh3d`` path used by crystal geometry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mat_viewer.loader import build_empty_bundle  # noqa: E402
from mat_viewer.presets import DEFAULT_STYLE  # noqa: E402
from mat_viewer.renderer import build_figure, implicit_entity  # noqa: E402


OUTPUT_DIR = Path(__file__).resolve().parent / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    scene = build_empty_bundle().scene
    scene["draw_atoms"] = []
    scene["bonds"] = []
    scene["geometry_entities"] = [
        implicit_entity(
            lambda points: np.sum(points**2, axis=1) - 25.0,
            ((-7.0, 7.0),) * 3,
            resolution=34,
            name="sphere",
            entity_id="implicit-sphere",
            color="#4F7CFF",
            opacity=1.0,
            flatshading=False,
            lighting={"ambient": 0.45, "diffuse": 0.8, "specular": 0.25},
        ),
        implicit_entity(
            lambda x, y, z: (
                (np.sqrt(x * x + y * y) - 3.5) ** 2 + z * z - 1.0
            ),
            ((-7.0, 7.0),) * 3,
            resolution=34,
            name="torus",
            entity_id="implicit-torus",
            color="#E45756",
            opacity=1.0,
            flatshading=False,
            lighting={"ambient": 0.4, "diffuse": 0.85, "specular": 0.2},
        ),
    ]
    style = {
        **DEFAULT_STYLE,
        "material": "mesh",
        "style": "ball",
        "projection": "orthographic",
        "show_axes": False,
        "show_axis_key": False,
        "show_labels": False,
        "show_unit_cell": False,
        "show_title": False,
        "background": "#FFFFFF",
    }
    figure = build_figure(scene, style)
    output = OUTPUT_DIR / "11_implicit_geometry.html"
    figure.write_html(str(output), include_plotlyjs="inline", full_html=True)
    print(f"✓ implicit geometry demo → {output}")


if __name__ == "__main__":
    main()
