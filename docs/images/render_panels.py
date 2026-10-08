"""README panels in the Figure 2d / 5a style.

Polyhedra: one highlighted A or B coordination hull at opacity 0.45, the
other hulls at 0.08. Disorder: atoms and bonds both use crystallographic
occupancy as opacity, which the CPU planner shares with the Plotly mesh path.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial import ConvexHull

from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from mat_viewer.render.cpu import render_rgba
from mat_viewer.render.planning import prepare_render
from mat_viewer.topology import extract_coordination_shell

ROOT = Path(__file__).resolve().parent
DAP7 = ROOT / "showcase_dap7.cif"
CAFFEINE = ROOT / "showcase_caffeine_pair.cif"

A_HIGHLIGHT = "#B89095"
B_HIGHLIGHT = "#A0AE83"
HULL_BG = "#C7C7C7"
HL_OPACITY = 0.45
BG_OPACITY = 0.08
ATOM_SCALE = 0.55
BOND_RADIUS = 0.10
WIDTH = 900
HEIGHT = 720


def _save(image: np.ndarray, path: Path) -> None:
    Image.fromarray(image).save(path)


def _polyhedra(bundle) -> list[dict]:
    """One highlighted A hull and one highlighted B hull; the rest stay faint.

    Same selection as Figure 5a: the A site nearest the cell centre, then the
    B site farthest from it in the a/c plane so the two hulls stay apart.
    """
    shells: dict[str, list[dict]] = {"A": [], "B": []}
    for fragment in bundle.topology_fragment_table or []:
        role = str(fragment.get("type") or "")
        if role not in shells:
            continue
        shell = extract_coordination_shell(
            bundle,
            int(fragment["index"]),
            cutoff=12.0,
            ligand_species=["ClO4"],
            level="molecule",
            center_kind="centroid",
        )
        coords = np.asarray(shell.get("shell_coords") or [], dtype=float)
        center = np.asarray(shell.get("center_coords") or [], dtype=float)
        if coords.ndim != 2 or len(coords) < 4 or center.shape != (3,):
            continue
        shells[role].append(
            {
                "index": int(fragment["index"]),
                "coords": coords,
                "center": center,
                "cn": int(shell["coordination_number"]),
            }
        )
    cell = np.asarray(bundle.M, dtype=float)
    corners = np.array(
        [
            i * cell[0] + j * cell[1] + k * cell[2]
            for i in (0, 1)
            for j in (0, 1)
            for k in (0, 1)
        ],
        dtype=float,
    )
    cell_center = 0.5 * (corners.min(axis=0) + corners.max(axis=0))
    a_pick = min(shells["A"], key=lambda rec: float(np.linalg.norm(rec["center"] - cell_center))) if shells["A"] else None
    b_pick = None
    if shells["B"]:
        if a_pick is None:
            b_pick = shells["B"][0]
        else:
            a_screen = a_pick["center"][[0, 2]]
            b_pick = max(shells["B"], key=lambda rec: float(np.linalg.norm(rec["center"][[0, 2]] - a_screen)))
    picked = {id(a_pick), id(b_pick)}
    polyhedra: list[dict] = []
    for role, records in shells.items():
        color = A_HIGHLIGHT if role == "A" else B_HIGHLIGHT
        for record in records:
            faces = ConvexHull(record["coords"]).simplices
            highlighted = id(record) in picked
            polyhedra.append(
                {
                    "vertices": record["coords"].tolist(),
                    "faces": np.asarray(faces, dtype=int).tolist(),
                    "color": color if highlighted else HULL_BG,
                    "opacity": HL_OPACITY if highlighted else BG_OPACITY,
                    "edge_opacity": HL_OPACITY if highlighted else BG_OPACITY,
                }
            )
    return polyhedra


def _render_polyhedron() -> None:
    bundle = build_loaded_crystal(name="DAP-7", cif_path=str(DAP7), source="local")
    scene = build_bundle_scene(
        bundle,
        display_mode="unit_cell",
        show_hydrogen=True,
        include_boundary_replicas=True,
    )
    scene = dict(scene)
    scene["polyhedra"] = _polyhedra(bundle)
    for bond in scene.get("bonds") or []:
        if bond.get("is_disordered"):
            bond["_render_opacity_scale"] = float(bond.get("occ", 1.0))
    plan = prepare_render(
        scene,
        view={"display": "unit_cell", "include_boundary_replicas": True},
        render={
            "backend": "cpu",
            "representation": "ball_stick",
            "width": WIDTH,
            "height": HEIGHT,
            "scale": 2,
            "show_hydrogen": True,
            "show_cell": True,
            "show_axes": False,
            "atom_scale": ATOM_SCALE,
            "bond_radius": BOND_RADIUS,
            "background": (1.0, 1.0, 1.0, 1.0),
        },
    )
    _save(render_rgba(plan, scale=2), ROOT / "panel_polyhedron.png")
    print("polyhedra", [(item["color"], item["opacity"], len(item["vertices"])) for item in scene["polyhedra"]])


def _render_disorder() -> None:
    bundle = build_loaded_crystal(name="caffeine", cif_path=str(CAFFEINE), source="local")
    scene = build_bundle_scene(
        bundle,
        display_mode="unit_cell",
        show_hydrogen=False,
        include_boundary_replicas=False,
    )
    scene = dict(scene)
    for bond in scene.get("bonds") or []:
        if bond.get("is_disordered"):
            bond["_render_opacity_scale"] = float(bond.get("occ", 1.0))
    plan = prepare_render(
        scene,
        render={
            "backend": "cpu",
            "representation": "ball_stick",
            "width": WIDTH,
            "height": HEIGHT,
            "scale": 2,
            "show_hydrogen": False,
            "show_cell": False,
            "atom_scale": ATOM_SCALE,
            "bond_radius": BOND_RADIUS,
            "background": (1.0, 1.0, 1.0, 1.0),
        },
    )
    _save(render_rgba(plan, scale=2), ROOT / "panel_disorder.png")
    print("disorder warnings", plan.warnings)


if __name__ == "__main__":
    _render_polyhedron()
    _render_disorder()
