from __future__ import annotations

from mat_viewer.render.selection import selection_outline_trace
from mat_viewer.render.picking import atom_instance_id


def _scene():
    return {
        "draw_atoms": [
            {"label": "A1", "cart": [0.0, 0.0, 0.0], "atom_radius": 0.2},
            {"label": "B1", "cart": [1.0, 0.0, 0.0], "atom_radius": 0.2},
        ]
    }


def test_selection_outline_trace_skips_empty_selection():
    assert selection_outline_trace(_scene(), {"atom_scale": 1.0}, selected_labels=set()) is None


def test_selection_outline_trace_emits_one_mesh_for_selected_labels():
    trace = selection_outline_trace(_scene(), {"atom_scale": 1.0}, selected_labels={"A1"})

    assert trace is not None
    payload = trace.to_plotly_json()
    assert payload["name"] == "selection-outline"
    assert payload["meta"]["mv_role"] == "selection"
    assert len(payload["x"]) > 0


def test_selection_outline_trace_supports_exact_atom_targets_and_empty_clear():
    scene = _scene()
    atom_id = atom_instance_id(scene["draw_atoms"][0], 0)
    trace = selection_outline_trace(scene, {"atom_scale": 1.0}, selected_targets={atom_id})
    assert trace is not None
    assert selection_outline_trace(
        scene, {"atom_scale": 1.0}, selected_labels=set(), selected_targets=set()
    ) is None
    assert selection_outline_trace(
        scene, {"atom_scale": 1.0}, selected_labels=set(), selected_targets={"atom:missing"}
    ) is None
    # Non-atom semantic targets are intentionally skipped by the atom outline
    # painter; their own geometry renderer owns any kind-specific highlight.
    assert selection_outline_trace(
        scene, {"atom_scale": 1.0}, selected_labels=set(), selected_targets={"polyhedron:oct:1"}
    ) is None
