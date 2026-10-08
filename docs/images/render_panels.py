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

from mat_viewer.agent import load_structure
from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from mat_viewer.render.contracts import CameraSpec
from mat_viewer.render.cpu import render_rgba
from mat_viewer.render.planning import prepare_render
from mat_viewer.topology import extract_coordination_shell

ROOT = Path(__file__).resolve().parent
DAP7 = ROOT / "showcase_dap7.cif"
CAFFEINE = ROOT / "showcase_caffeine_pair.cif"
PETN = ROOT / "showcase_petn_molecule.xyz"
PETN_VECTORS = ROOT / "showcase_petn_vectors.json"

A_HIGHLIGHT = "#3B6EA5"
B_HIGHLIGHT = "#E09F3E"
HULL_BG = "#C8C8C8"
HL_OPACITY = 0.45
BG_OPACITY = 0.08
ATOM_SCALE = 0.55
BOND_RADIUS = 0.10
WIDTH = 900
HEIGHT = 720


def _save(image: np.ndarray, path: Path) -> None:
    Image.fromarray(image).save(path)


def _axis_frame(matrix: np.ndarray) -> np.ndarray:
    """Map the cell so a lies on screen x, c on screen z, and b along the view."""
    lattice = np.asarray(matrix, dtype=float)
    a_vec, b_vec, c_vec = lattice
    z_axis = c_vec / max(float(np.linalg.norm(c_vec)), 1e-8)
    x_axis = a_vec - float(np.dot(a_vec, z_axis)) * z_axis
    if float(np.linalg.norm(x_axis)) < 1e-8:
        x_axis = b_vec - float(np.dot(b_vec, z_axis)) * z_axis
    x_axis = x_axis / max(float(np.linalg.norm(x_axis)), 1e-8)
    y_axis = np.cross(z_axis, x_axis)
    if float(np.dot(y_axis, b_vec)) < 0.0:
        y_axis = -y_axis
    return np.stack([x_axis, y_axis, z_axis], axis=0)


def _rotate_point(frame: np.ndarray, point) -> list[float]:
    return (frame @ np.asarray(point, dtype=float)).tolist()


def _rotate_scene(scene: dict, frame: np.ndarray) -> None:
    for key in ("draw_atoms", "atoms"):
        for atom in scene.get(key) or []:
            if "cart" in atom:
                atom["cart"] = _rotate_point(frame, atom["cart"])
    for bond in scene.get("bonds") or []:
        if "start" in bond:
            bond["start"] = _rotate_point(frame, bond["start"])
        if "end" in bond:
            bond["end"] = _rotate_point(frame, bond["end"])
    for polyhedron in scene.get("polyhedra") or []:
        polyhedron["vertices"] = [
            _rotate_point(frame, vertex) for vertex in polyhedron.get("vertices") or []
        ]
    lattice = np.asarray(scene.get("M") if scene.get("M") is not None else scene.get("matrix"), dtype=float)
    rotated = lattice @ frame.T
    scene["M"] = rotated
    scene["matrix"] = rotated


def _axis_camera(scene: dict, *, width: int, height: int) -> CameraSpec:
    points = []
    for atom in scene.get("draw_atoms") or scene.get("atoms") or []:
        if "cart" in atom:
            points.append(np.asarray(atom["cart"], dtype=float))
    for bond in scene.get("bonds") or []:
        if "start" in bond and "end" in bond:
            points.append(np.asarray(bond["start"], dtype=float))
            points.append(np.asarray(bond["end"], dtype=float))
    for polyhedron in scene.get("polyhedra") or []:
        vertices = np.asarray(polyhedron.get("vertices") or [], dtype=float)
        if len(vertices):
            points.extend(vertices)
    lattice = np.asarray(scene["M"], dtype=float)
    for i in (0, 1):
        for j in (0, 1):
            for k in (0, 1):
                points.append(i * lattice[0] + j * lattice[1] + k * lattice[2])
    cloud = np.vstack(points)
    minimum = cloud.min(axis=0)
    maximum = cloud.max(axis=0)
    target = 0.5 * (minimum + maximum)
    radius = max(float(np.linalg.norm(cloud - target, axis=1).max()), 0.5)
    distance = max(radius * 3.2, 2.0)
    aspect = width / height
    half_x = 0.5 * float(maximum[0] - minimum[0]) + 0.6
    half_z = 0.5 * float(maximum[2] - minimum[2]) + 0.6
    return CameraSpec.looking_along(
        (0.0, -1.0, 0.0),
        target=target,
        up=(0.0, 0.0, 1.0),
        distance=distance,
        projection="orthographic",
        ortho_scale=max(half_z, half_x / aspect),
        near=max(1.0e-3, distance - radius * 2.2),
        far=distance + radius * 2.2,
    )


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
    _rotate_scene(scene, _axis_frame(np.asarray(bundle.M, dtype=float)))
    plan = prepare_render(
        scene,
        view={"display": "unit_cell", "include_boundary_replicas": True},
        camera=_axis_camera(scene, width=WIDTH, height=HEIGHT),
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


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        return vector
    return vector / norm


def _write_mode_arrows() -> list[dict]:
    """Nitrate stretch plus a sideways arrow on each terminal oxygen.

    The stretch lies on the N–O bond and disappears into the stick. The
    second arrow is perpendicular to that bond, so the displacement stays
    visible beside the molecule.
    """
    lines = PETN.read_text(encoding="utf-8").splitlines()
    count = int(lines[0].split()[0])
    atoms = []
    for line in lines[2 : 2 + count]:
        element, x, y, z = line.split()[:4]
        atoms.append((element, np.array([float(x), float(y), float(z)])))
    arrows = []
    for index, (element, origin) in enumerate(atoms):
        if element != "O":
            continue
        nitrogen = None
        carbon = None
        for other, position in atoms:
            distance = float(np.linalg.norm(position - origin))
            if other == "N" and distance < 1.7:
                nitrogen = position
            if other == "C" and distance < 1.7:
                carbon = position
        if nitrogen is None or carbon is not None:
            continue
        along = _unit(origin - nitrogen)
        reference = np.array([0.0, 0.0, 1.0]) if abs(float(along[2])) < 0.85 else np.array([1.0, 0.0, 0.0])
        sideways = _unit(np.cross(along, reference))
        arrows.append({"id": f"stretch-{index}", "origin": origin.round(4).tolist(), "vector": (along * 1.35).round(4).tolist()})
        arrows.append({"id": f"wag-{index}", "origin": origin.round(4).tolist(), "vector": (sideways * 1.25).round(4).tolist()})
    payload = [
        {
            "id": "mode",
            "magnitude_mode": "absolute",
            "anchor": "center",
            "viewport_policy": "include",
            "color": "#D55E00",
            "note": "Mock nitrate motion. Stretch arrows follow N-O; wag arrows are perpendicular to the bond. Not a phonon.",
            "style": {
                "shaft_radius": 0.075,
                "head_radius_ratio": 2.4,
                "head_length_ratio": 0.34,
                "sides": 16,
            },
            "arrows": arrows,
        }
    ]
    PETN_VECTORS.write_text(json_dumps(payload), encoding="utf-8")
    return payload


def json_dumps(payload) -> str:
    import json

    return json.dumps(payload, indent=2) + "\n"


def _render_mode() -> None:
    overlays = _write_mode_arrows()
    structure = load_structure(PETN)
    plan = prepare_render(
        structure,
        vector_overlays=overlays,
        render={
            "backend": "cpu",
            "representation": "ball_stick",
            "width": WIDTH,
            "height": HEIGHT,
            "scale": 2,
            "show_hydrogen": True,
            "show_cell": False,
            "atom_scale": 0.72,
            "bond_radius": 0.10,
            "background": (1.0, 1.0, 1.0, 1.0),
        },
    )
    _save(render_rgba(plan, scale=2), ROOT / "panel_mode.png")
    print("mode arrows", len(overlays[0]["arrows"]))


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
    _render_mode()
