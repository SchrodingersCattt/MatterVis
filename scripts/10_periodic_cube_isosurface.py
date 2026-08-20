#!/usr/bin/env python3
"""Generate a before/after regression image for periodic cube isosurfaces."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mat_viewer.cube import build_cube_figure, export_static

OUTPUT = ROOT / "scripts" / "_outputs"


def write_cube(path: Path) -> None:
    shape = (24, 25, 26)
    u = np.arange(shape[0]) / shape[0]
    v = np.arange(shape[1]) / shape[1]
    w = np.arange(shape[2]) / shape[2]
    du = np.minimum(u, 1.0 - u)[:, None, None]
    dv = (v - 0.5)[None, :, None]
    dw = (w - 0.5)[None, None, :]
    values = np.exp(-0.5 * ((du / 0.16) ** 2 + (dv / 0.14) ** 2 + (dw / 0.14) ** 2))
    lattice = np.array([[5.0, 0.0, 0.0], [0.0, 5.4, 0.0], [-1.0, 0.0, 4.8]])
    axes_bohr = lattice / np.array(shape)[:, None] / 0.529177210903
    with path.open("w") as handle:
        handle.write("Periodic cosine scalar field\nSynthetic regression\n")
        handle.write("    1 0.0 0.0 0.0\n")
        for count, axis in zip(shape, axes_bohr):
            handle.write(f"{count:5d}" + "".join(f" {value:12.7f}" for value in axis) + "\n")
        atom_bohr = np.array([0.0, 0.5, 0.5]) @ lattice / 0.529177210903
        handle.write("    6 6.0" + "".join(f" {value:12.7f}" for value in atom_bohr) + "\n")
        flat = values.ravel()
        for start in range(0, flat.size, 6):
            handle.write("".join(f"{value:13.5e}" for value in flat[start:start + 6]) + "\n")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cube_path = OUTPUT / "10_periodic_cube_isosurface.cube"
    write_cube(cube_path)
    camera = {
        "eye": {"x": 1.35, "y": 1.2, "z": 1.15},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 0.0, "z": 1.0},
        "projection": {"type": "orthographic"},
    }
    panels = [
        build_cube_figure(
            cube_path, isovalue=0.35, periodic=periodic, camera=camera,
            display_mode="unit_cell", style={"isosurface_stride": 1},
        )
        for periodic in (False, True)
    ]
    fig = make_subplots(
        rows=1, cols=2, specs=[[{"type": "scene"}, {"type": "scene"}]],
        subplot_titles=("Open finite grid", "Periodic endpoint closure"),
    )
    for column, panel in enumerate(panels, start=1):
        for trace in panel.data:
            fig.add_trace(trace, row=1, col=column)
        scene_name = "scene" if column == 1 else f"scene{column}"
        domain = getattr(fig.layout, scene_name).domain.to_plotly_json()
        scene = panel.layout.scene.to_plotly_json()
        scene["domain"] = domain
        fig.update_layout(**{scene_name: scene, f"{scene_name}_camera": camera})
    fig.update_layout(
        title={"text": "Periodic scalar-grid isosurface regression", "x": 0.5},
        showlegend=False,
        margin={"l": 0, "r": 0, "t": 65, "b": 0},
    )
    output = OUTPUT / "10_periodic_cube_isosurface.png"
    export_static(fig, output, width=1500, height=700, scale=2)
    print(output)


if __name__ == "__main__":
    main()