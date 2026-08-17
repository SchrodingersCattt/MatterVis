from __future__ import annotations

import gemmi
import numpy as np

from crystal_viewer.render.figures import build_figure, build_row_figure
from crystal_viewer.render.selection import _atom_selection_trace, _bond_selection_trace
from crystal_viewer.scene import build_scene_from_atoms, scene_ops, scene_style


def _scene():
    cell = gemmi.UnitCell(20.0, 20.0, 20.0, 90.0, 90.0, 90.0)
    matrix = np.eye(3) * 20.0
    atoms = [
        {"label": "C1", "elem": "C", "frac": np.array([0.5, 0.5, 0.5]), "cart": np.array([0.0, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
        {"label": "H2", "elem": "H", "frac": np.array([0.55, 0.5, 0.5]), "cart": np.array([1.09, 0.0, 0.0]), "occ": 1.0, "dg": ".", "da": "."},
    ]
    return build_scene_from_atoms(
        name="ch",
        title="CH",
        atoms=atoms,
        cell=cell,
        M=matrix,
        R=np.eye(3),
        show_hydrogen=True,
        preset={"style": {"show_hydrogen": True}},
        display_mode="cluster",
        ops=scene_ops(),
    )


def _roles(figure) -> set[str]:
    return {
        trace.meta.get("mv_role")
        for trace in figure.data
        if isinstance(trace.meta, dict) and trace.meta.get("mv_role")
    }


def test_interaction_markers_have_no_default_white_outline() -> None:
    scene = _scene()
    style = scene_style(scene, {"show_hydrogen": True})
    atom = _atom_selection_trace(scene, style).to_plotly_json()
    bond = _bond_selection_trace(scene, style).to_plotly_json()
    for trace in (atom, bond):
        assert trace["marker"]["line"]["width"] == 0
        assert trace["marker"]["line"]["color"] == "rgba(0,0,0,0)"


def test_static_figure_omits_interaction_only_traces() -> None:
    scene = _scene()
    style = scene_style(scene, {"show_hydrogen": True})
    interactive = build_figure(scene, style, include_interaction_traces=True)
    static = build_figure(scene, style, include_interaction_traces=False)
    assert {"atom_selection", "bond_selection"} <= _roles(interactive)
    assert "atom_selection" not in _roles(static)
    assert "bond_selection" not in _roles(static)
    assert "disorder_preview" not in _roles(static)


def test_static_row_figure_omits_atom_selection_trace() -> None:
    scene = _scene()
    style = scene_style(scene, {"show_hydrogen": True})
    interactive = build_row_figure([(scene, style)], include_interaction_traces=True)
    static = build_row_figure([(scene, style)], include_interaction_traces=False)
    assert "atom_selection" in _roles(interactive)
    assert "atom_selection" not in _roles(static)
