from __future__ import annotations

import pytest

from crystal_viewer.render.style import _atom_effective_opacity
from crystal_viewer.render.traces_atoms import (
    _atom_mesh_traces,
    _atom_scatter_traces,
    _bond_segments,
)
from crystal_viewer.render.traces_overlays import _minor_outline_traces
from crystal_viewer.style.disorder import bond_effective_opacity


def _atom(label: str, *, is_minor: bool) -> dict:
    return {
        "label": label,
        "elem": "C",
        "cart": [0.0 if not is_minor else 2.0, 0.0, 0.0],
        "atom_radius": 0.18,
        "color": "#112233",
        "color_light": "#AABBCC",
        "is_minor": is_minor,
        "occ": 0.4,
        "uiso": 0.04,
        "U": None,
    }


def _style(**overrides) -> dict:
    return {
        "atom_scale": 1.0,
        "bond_radius": 0.1,
        "disorder": "opacity",
        "major_opacity": 1.0,
        "minor_opacity": 0.35,
        "show_minor_only": False,
        **overrides,
    }


@pytest.mark.parametrize("trace_builder", (_atom_mesh_traces, _atom_scatter_traces))
def test_partial_occupancy_does_not_make_major_atom_look_minor(trace_builder):
    scene = {"draw_atoms": [_atom("major", is_minor=False)]}

    trace = trace_builder(scene, _style())[0]
    if trace["type"] == "scatter3d":
        assert trace["marker"]["color"] == "#112233"
        assert trace["marker"]["opacity"] == 1.0
    else:
        assert trace["color"] == "#112233"
        assert trace["opacity"] == 1.0
    assert trace["meta"]["mv_minor"] is False


@pytest.mark.parametrize("trace_builder", (_atom_mesh_traces, _atom_scatter_traces))
def test_loader_minor_atom_keeps_minor_styling(trace_builder):
    scene = {"draw_atoms": [_atom("minor", is_minor=True)]}

    trace = trace_builder(scene, _style())[0]
    if trace["type"] == "scatter3d":
        assert trace["marker"]["color"] == "#AABBCC"
        assert trace["marker"]["opacity"] == 0.4
    else:
        assert trace["color"] == "#AABBCC"
        assert trace["opacity"] == 0.4
    assert trace["meta"]["mv_minor"] is True


def test_minor_only_filter_uses_loader_identity_not_occupancy():
    scene = {
        "draw_atoms": [
            _atom("major", is_minor=False),
            _atom("minor", is_minor=True),
        ]
    }

    traces = _atom_scatter_traces(scene, _style(show_minor_only=True))

    assert len(traces) == 1
    assert traces[0]["meta"]["mv_minor"] is True


def test_minor_outline_uses_loader_identity_not_occupancy():
    major_scene = {"draw_atoms": [_atom("major", is_minor=False)]}
    minor_scene = {"draw_atoms": [_atom("minor", is_minor=True)]}
    style = _style(disorder="outline_rings")

    assert _minor_outline_traces(major_scene, style) == []
    outlines = _minor_outline_traces(minor_scene, style)
    assert len(outlines) == 1
    assert outlines[0]["color"] == "#AABBCC"


def test_partial_occupancy_does_not_make_major_bond_minor():
    atoms = [_atom("A", is_minor=False), _atom("B", is_minor=False)]
    atoms[1]["cart"] = [1.0, 0.0, 0.0]
    bond = {
        "i": 0,
        "j": 1,
        "start": atoms[0]["cart"],
        "end": atoms[1]["cart"],
        "color_i": "#112233",
        "color_j": "#112233",
        "is_minor": False,
        "occ": 0.4,
    }
    scene = {"draw_atoms": atoms, "bonds": [bond]}

    assert list(_bond_segments(scene, _style(show_minor_only=True))) == []
    assert bond_effective_opacity(bond, _style()) == 1.0


def test_occupancy_scales_only_loader_confirmed_minor_components():
    major = _atom("major", is_minor=False)
    minor = _atom("minor", is_minor=True)

    assert _atom_effective_opacity(major, _style()) == 1.0
    assert _atom_effective_opacity(minor, _style()) == 0.4
    assert bond_effective_opacity({"is_minor": True, "occ": 0.4}, _style()) == 0.4
