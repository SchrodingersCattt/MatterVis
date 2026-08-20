"""Render native world-space polarization arrows crossing atom geometry."""
from __future__ import annotations

import sys
from pathlib import Path

import gemmi
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from crystal_viewer.renderer import build_figure
from crystal_viewer.scene import build_scene_from_atoms, scene_ops, scene_style

OUTPUT_DIR = Path(__file__).resolve().parent / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> None:
    cell = gemmi.UnitCell(12, 12, 12, 90, 90, 90)
    lattice = np.eye(3) * 12
    atoms = [
        {"label": "C1", "elem": "C", "frac": np.array([0.5, 0.5, 0.5]), "cart": np.array([0.0, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
        {"label": "N2", "elem": "N", "frac": np.array([0.61, 0.5, 0.5]), "cart": np.array([1.35, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
    ]
    scene = build_scene_from_atoms(name="world-vectors", title="World vectors", atoms=atoms, cell=cell, M=lattice, R=np.eye(3), display_mode="cluster", ops=scene_ops())
    style = scene_style(scene, {"show_title": False, "projection": "orthographic", "show_axes": False, "show_unit_cell": False})
    vectors = [{
        "id": "polarization",
        "name": "Polarization",
        "magnitude_mode": "normalized",
        "length": 3.0,
        "viewport_policy": "include",
        "style": {"shaft_radius": 0.10, "head_radius_ratio": 2.2, "sides": 16},
        "arrows": [{"id": "p0", "origin": [0.0, 0.0, 0.0], "vector": [1.0, 0.5, 0.2], "color": "#D55E00", "label": "P"}],
    }]
    for suffix, eye in (("front", (1.5, -1.8, 1.1)), ("back", (-1.5, 1.8, 1.1))):
        local_style = {**style, "camera": {"eye": {"x": eye[0], "y": eye[1], "z": eye[2]}, "center": {"x": 0, "y": 0, "z": 0}, "up": {"x": 0, "y": 0, "z": 1}, "projection": {"type": "orthographic"}}}
        figure = build_figure(scene, local_style, vector_overlays=vectors, include_interaction_traces=False)
        figure.write_image(str(OUTPUT_DIR / f"12_world_vector_{suffix}.png"), width=800, height=620, scale=2)
    print(OUTPUT_DIR / "12_world_vector_front.png")
    print(OUTPUT_DIR / "12_world_vector_back.png")


if __name__ == "__main__":
    main()
