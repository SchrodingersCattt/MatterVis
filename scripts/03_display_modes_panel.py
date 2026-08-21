"""Render the same CIF in three display modes side-by-side.

Run from the repository root:

    python scripts/03_display_modes_panel.py

Produces ``03_display_modes.png`` with three subplots:

* formula unit  (the canonical molecular formula)
* unit cell     (every atom inside the lattice box)
* topology      (one A-site cation + its coordination polyhedron)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from plotly.subplots import make_subplots  # noqa: E402

from mat_viewer.loader import build_bundle_scene, build_loaded_crystal  # noqa: E402
from mat_viewer.renderer import build_figure  # noqa: E402
from mat_viewer.scene import scene_style  # noqa: E402
from mat_viewer.topology import analyze_topology  # noqa: E402


HERE = Path(__file__).resolve().parent
CIF = HERE / "data" / "DAP-4.cif"
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def first_a_site(bundle):
    for frag in bundle.topology_fragment_table:
        if frag["type"] == "A":
            return frag
    raise SystemExit("No A-site fragment in DAP-4 (unexpected).")


def main() -> None:
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4")
    target = first_a_site(bundle)

    formula_scene = build_bundle_scene(bundle, display_mode="formula_unit")
    cell_scene = build_bundle_scene(bundle, display_mode="unit_cell")
    topology = analyze_topology(bundle, center_index=target["index"], cutoff=8.0)

    panels = [
        ("Formula unit", formula_scene, {"show_unit_cell": True}, None),
        ("Unit cell",    cell_scene,    {"show_unit_cell": True, "atom_scale": 0.85, "bond_radius": 0.13}, None),
        (
            f"A-site shell (CN={topology['coordination_number']})",
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

    for col, (title, scene, style_overrides, topology_data) in enumerate(panels, start=1):
        style = scene_style(scene, {
            "show_axes": False,
            "show_labels": False,
            **style_overrides,
        })
        sub_fig = build_figure(scene, style, topology_data=topology_data)
        for trace in sub_fig.data:
            fig.add_trace(trace, row=1, col=col)
        scene_key = "scene" if col == 1 else f"scene{col}"
        fig.layout[scene_key].update(sub_fig.layout.scene.to_plotly_json())

    fig.update_layout(
        title=dict(text="DAP-4: formula unit -> unit cell -> coordination shell", x=0.5),
        paper_bgcolor="white",
        margin=dict(l=10, r=10, t=70, b=10),
        showlegend=False,
    )

    out = OUTPUT_DIR / "03_display_modes.png"
    fig.write_image(str(out), width=1500, height=560, scale=2)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
