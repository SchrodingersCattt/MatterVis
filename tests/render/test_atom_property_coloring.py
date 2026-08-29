"""Render-plan integration tests for continuous atom colours."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from ase import Atoms
from ase.io import write

from mat_viewer.properties import AtomPropertyColorSpec, build_color_lut
from mat_viewer.loader.structure_input import load_structure_input
from mat_viewer.render.contracts import LinePrimitive, TriangleMeshPrimitive
from mat_viewer.render.planning import prepare_render


def _source(tmp_path: Path):
    atoms = Atoms(
        "CO",
        positions=[[0.0, 0.0, 0.0], [1.4, 0.0, 0.0]],
        cell=[8.0, 8.0, 8.0],
        pbc=True,
    )
    atoms.arrays["charge"] = np.asarray([-1.0, 1.0])
    path = tmp_path / "charges.extxyz"
    write(path, atoms, format="extxyz")
    return load_structure_input(path, frame_indices=[0])


def _atom_rgba(plan):
    return {
        int(primitive.metadata["source_index"]): primitive.rgba
        for primitive in plan.primitives
        if isinstance(primitive, TriangleMeshPrimitive)
        and primitive.metadata.get("kind") == "atom"
    }


def _bond_rgba(plan):
    return [
        primitive.rgba
        for primitive in plan.primitives
        if primitive.metadata.get("kind") == "bond"
    ]


def test_property_colours_use_shared_lut_and_reserve_colorbar(tmp_path: Path) -> None:
    plan = prepare_render(
        _source(tmp_path),
        render={"show_cell": False},
        atom_property_color=AtomPropertyColorSpec(fields=("array:charges",)),
    )
    colors = _atom_rgba(plan)
    lut = build_color_lut("viridis").astype(float) / 255.0
    assert colors[0] == tuple(lut[0])
    assert colors[1] == tuple(lut[-1])
    assert tuple(lut[0]) in _bond_rgba(plan)
    assert tuple(lut[-1]) in _bond_rgba(plan)
    assert plan.viewports[0].rect[2] < 1.0
    assert plan.metadata["atom_property_color"]["range"] == [-1.0, 1.0]
    assert plan.metadata["atom_property_color"]["range_scope"] == (
        "selected_source_frames_and_atoms"
    )


def test_atom_group_colour_overrides_property_base_colour(tmp_path: Path) -> None:
    plan = prepare_render(
        _source(tmp_path),
        render={"show_cell": False},
        atom_property_color={"fields": ["charges"], "show_colorbar": False},
        atom_groups=[{"selector": {"elements": ["O"]}, "color": "#FF00FF"}],
    )
    colors = _atom_rgba(plan)
    assert colors[1] == (1.0, 0.0, 1.0, 1.0)
    assert (1.0, 0.0, 1.0, 1.0) in _bond_rgba(plan)
    assert plan.viewports[0].rect == (0.0, 0.0, 1.0, 1.0)


def test_bond_group_colour_overrides_both_property_coloured_halves(
    tmp_path: Path,
) -> None:
    plan = prepare_render(
        _source(tmp_path),
        render={"show_cell": False},
        atom_property_color={"fields": ["charges"]},
        bond_groups=[{"selector": {"all": True}, "color": "#00FF00"}],
    )
    assert set(_bond_rgba(plan)) == {(0.0, 1.0, 0.0, 1.0)}


def test_wireframe_bond_uses_two_property_coloured_halves(tmp_path: Path) -> None:
    plan = prepare_render(
        _source(tmp_path),
        render={"show_cell": False, "representation": "wireframe"},
        atom_property_color={"fields": ["charges"]},
    )
    lines = [
        primitive
        for primitive in plan.primitives
        if isinstance(primitive, LinePrimitive)
        and primitive.metadata.get("kind") == "bond"
    ]
    assert len(lines) == 2
    assert len({line.rgba for line in lines}) == 2


def test_web_bond_segments_inherit_property_colours_and_allow_override() -> None:
    from mat_viewer.renderer import _bond_segments

    atoms = [
        {"cart": [0.0, 0.0, 0.0], "_property_color": "#112233"},
        {"cart": [1.0, 0.0, 0.0], "_property_color": "#AABBCC"},
    ]
    bond = {
        "i": 0,
        "j": 1,
        "start": [0.0, 0.0, 0.0],
        "end": [1.0, 0.0, 0.0],
        "color_i": "#FF0000",
        "color_j": "#00FF00",
    }
    style = {"atom_property_color": {"fields": ["array:charges"]}}
    assert [item[0] for item in _bond_segments({"draw_atoms": atoms, "bonds": [bond]}, style)] == [
        "#112233",
        "#AABBCC",
    ]
    bond["_render_color"] = "#010203"
    assert [item[0] for item in _bond_segments({"draw_atoms": atoms, "bonds": [bond]}, style)] == [
        "#010203",
        "#010203",
    ]


def test_no_property_keeps_full_viewport_and_omits_metadata(tmp_path: Path) -> None:
    plan = prepare_render(_source(tmp_path), render={"show_cell": False})
    assert plan.viewports[0].rect == (0.0, 0.0, 1.0, 1.0)
    assert plan.metadata["atom_property_color"] is None


def test_plotly_batches_continuous_atoms_and_keeps_group_override_small(
    tmp_path: Path,
) -> None:
    pytest = __import__("pytest")
    pytest.importorskip("plotly")
    from mat_viewer.render.plotly import build_figure

    plan = prepare_render(
        _source(tmp_path),
        render={"show_cell": False},
        atom_property_color={"fields": ["charges"]},
        atom_groups=[{"selector": {"elements": ["O"]}, "color": "#FF00FF"}],
    )
    figure = build_figure(plan)
    atom_meshes = [
        trace
        for trace in figure.data
        if trace.type == "mesh3d" and str(trace.name).startswith("atom")
    ]
    assert len(atom_meshes) == 2
    assert sum(trace.vertexcolor is not None for trace in atom_meshes) == 1
    colorbars = [
        trace
        for trace in figure.data
        if (trace.meta or {}).get("mv_role") == "atom_property_colorbar"
    ]
    assert len(colorbars) == 1


def test_cpu_svg_colorbar_remains_vector_geometry(tmp_path: Path) -> None:
    from mat_viewer.render.cpu.vector import render_vector

    plan = prepare_render(
        _source(tmp_path),
        render={"show_cell": False, "width": 320, "height": 240},
        atom_property_color={"fields": ["charges"]},
    )
    result = render_vector(plan, format="svg")
    svg = result.data.decode("utf-8")
    assert "<image" not in svg
    assert svg.count("<path") >= 256
