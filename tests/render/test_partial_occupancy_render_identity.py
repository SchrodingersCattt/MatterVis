from __future__ import annotations

import numpy as np
import pytest

from mat_viewer.render.style import _atom_effective_opacity, _style_trace_dicts
from mat_viewer.render.traces_atoms import (
    _atom_mesh_traces,
    _atom_scatter_traces,
    _bond_segments,
    _flat_highlight_center,
)
from mat_viewer.render.traces_overlays import _minor_outline_traces
from mat_viewer.style.disorder import atom_is_disordered, bond_effective_opacity


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
def test_partial_occupancy_does_not_make_ordered_atom_translucent(trace_builder):
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


def test_flat_atoms_with_different_occupancies_keep_distinct_opacities():
    minor_low = _atom("minor-low", is_minor=True)
    minor_high = _atom("minor-high", is_minor=True)
    minor_high["occ"] = 0.7

    traces = _atom_scatter_traces(
        {"draw_atoms": [minor_low, minor_high]},
        _style(),
    )

    assert sorted(trace["marker"]["opacity"] for trace in traces) == [0.4, 0.7]


def test_flat_sizes_follow_radius_and_use_opaque_upper_right_highlights():
    major = _atom("major", is_minor=False)
    minor = _atom("minor", is_minor=True)
    minor["atom_radius"] = 0.24
    scene = {
        "draw_atoms": [major, minor],
        "view_x": [1.0, 0.0, 0.0],
        "view_y": [0.0, 1.0, 0.0],
        "view_z": [0.0, 0.0, 1.0],
    }

    camera = {
        "eye": {"x": 0.0, "y": 0.0, "z": 1.0},
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "up": {"x": 0.0, "y": 1.0, "z": 0.0},
    }
    style = _style(material="flat", camera=camera)
    traces = _atom_scatter_traces(scene, style)
    delta = _flat_highlight_center(major, scene, style) - np.asarray(major["cart"], dtype=float)
    assert delta[0] > 0.0 and delta[1] > 0.0
    assert delta[0] == pytest.approx(delta[1])
    atom_traces = [trace for trace in traces if trace["meta"]["mv_role"] == "atom"]
    highlight_traces = [
        trace for trace in traces if trace["meta"]["mv_role"] == "atom_highlight"
    ]
    sizes = [float(size) for trace in atom_traces for size in trace["marker"]["size"]]
    assert len(set(sizes)) == 2
    assert len(highlight_traces) == len(atom_traces)
    assert {trace["meta"]["mv_highlight_kind"] for trace in highlight_traces} == {"core"}
    assert all(trace["marker"]["color"] == "#FFFFFF" for trace in highlight_traces)
    assert all(trace["marker"]["opacity"] == 1.0 for trace in highlight_traces)


def test_flat_cache_replay_preserves_occupancy_opacity():
    traces = [
        {
            "type": "scatter3d",
            "marker": {"opacity": 0.4},
            "meta": {"mv_role": "atom", "mv_minor": True},
        }
    ]

    styled = _style_trace_dicts(traces, _style())

    assert styled[0]["marker"]["opacity"] == 0.4

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


def test_occupancy_scales_only_loader_confirmed_disordered_components():
    ordered = _atom("ordered", is_minor=False)
    disordered_major = _atom("disordered-major", is_minor=False)
    disordered_major["is_disordered"] = True
    disordered_major["occ"] = 0.7
    disordered_low = _atom("disordered-low", is_minor=False)
    disordered_low["is_disordered"] = True
    disordered_low["occ"] = 0.02
    minor = _atom("minor", is_minor=True)

    assert _atom_effective_opacity(ordered, _style()) == 1.0
    assert _atom_effective_opacity(disordered_major, _style()) == 0.7
    assert _atom_effective_opacity(disordered_low, _style()) == 0.02
    assert _atom_effective_opacity(minor, _style()) == 0.4
    assert bond_effective_opacity(
        {"is_minor": False, "is_disordered": True, "occ": 0.7},
        _style(),
    ) == 0.7
    assert bond_effective_opacity({"is_minor": True, "occ": 0.4}, _style()) == 0.4


def test_loader_provenance_distinguishes_disorder_from_partial_occupancy():
    assert atom_is_disordered({"occ": 0.5}) is False
    assert atom_is_disordered({"occ": 0.5, "_is_minor": False}) is True
    assert atom_is_disordered({"occ": 0.5, "_is_minor": True}) is True
