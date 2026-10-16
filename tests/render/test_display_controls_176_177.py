from __future__ import annotations

import numpy as np

from mat_viewer.loader import build_empty_bundle
from mat_viewer.render.planning import prepare_render
from mat_viewer.renderer import build_figure


def _scene(*, minor: bool = False) -> dict:
    scene = build_empty_bundle().scene
    scene["draw_atoms"] = [
        {
            "label": "H1",
            "elem": "H",
            "cart": [0.0, 0.0, 0.0],
            "atom_radius": 0.16,
            "color": "#FFFFFF",
            "color_light": "#EEEEEE",
            "is_minor": minor,
            "is_disordered": minor,
            "occ": 0.5 if minor else 1.0,
        },
        {
            "label": "O1",
            "elem": "O",
            "cart": [1.2, 0.0, 0.0],
            "atom_radius": 0.16,
            "color": "#B85060",
            "color_light": "#D48A88",
            "is_minor": False,
            "is_disordered": False,
        },
    ]
    scene["bonds"] = [
        {
            "i": 0,
            "j": 1,
            "start": [0.0, 0.0, 0.0],
            "end": [1.2, 0.0, 0.0],
            "color_i": "#FFFFFF",
            "color_j": "#B85060",
            "is_minor": minor,
            "is_disordered": minor,
            "occ": 0.5 if minor else 1.0,
        }
    ]
    return scene


def _atom_primitives(plan):
    return [item for item in plan.primitives if item.metadata.get("kind") == "atom"]


def test_hydrogen_radius_has_bond_relative_floor_and_override():
    scene = _scene()
    plan = prepare_render(
        scene,
        render={"representation": "ball_stick", "bond_radius": 0.2, "show_cell": False},
    )
    primitive = _atom_primitives(plan)[0]
    center = np.mean(primitive.vertices, axis=0)
    assert np.max(np.linalg.norm(primitive.vertices - center, axis=1)) >= 0.3

    overridden = prepare_render(
        scene,
        render={
            "representation": "ball_stick",
            "bond_radius": 0.2,
            "min_atom_radius": 0.42,
            "show_cell": False,
        },
    )
    primitive = _atom_primitives(overridden)[0]
    center = np.mean(primitive.vertices, axis=0)
    assert np.max(np.linalg.norm(primitive.vertices - center, axis=1)) >= 0.42


def test_ball_style_omits_bonds_while_ball_stick_keeps_them():
    style = {
        "material": "mesh",
        "disorder": "none",
        "atom_scale": 1.0,
        "bond_radius": 0.1,
        "show_axes": False,
        "show_labels": False,
        "topology_enabled": False,
    }
    ball = build_figure(_scene(), {**style, "style": "ball"})
    ball_stick = build_figure(_scene(), {**style, "style": "ball_stick"})
    ball_bonds = [
        trace for trace in ball.data if (trace.meta or {}).get("mv_role") == "bond"
    ]
    stick_bonds = [
        trace
        for trace in ball_stick.data
        if (trace.meta or {}).get("mv_role") == "bond"
    ]
    assert not ball_bonds
    assert stick_bonds


def test_minor_only_keeps_ordered_structure_visible():
    style = {
        "material": "mesh",
        "style": "ball_stick",
        "disorder": "none",
        "atom_scale": 1.0,
        "show_minor_only": True,
        "bond_radius": 0.1,
        "show_axes": False,
        "show_labels": False,
        "topology_enabled": False,
    }
    figure = build_figure(_scene(minor=False), style)
    atoms = [
        trace for trace in figure.data if (trace.meta or {}).get("mv_role") == "atom"
    ]
    assert atoms
    assert all(trace.visible is not False for trace in atoms)


def test_zero_bond_group_opacity_suppresses_minor_wireframe():
    style = {
        "material": "mesh",
        "style": "ball_stick",
        "disorder": "outline_rings",
        "atom_scale": 1.0,
        "minor_wireframe": True,
        "bond_radius": 0.1,
        "show_axes": False,
        "show_labels": False,
        "topology_enabled": False,
        "bond_groups": [
            {"id": "hide", "selector": {"all": True}, "opacity": 0.0}
        ],
    }
    figure = build_figure(_scene(minor=True), style)
    traces = [
        trace for trace in figure.data if (trace.meta or {}).get("mv_role") == "bond"
    ]
    assert all(float(trace.opacity or 0.0) == 0.0 for trace in traces)
