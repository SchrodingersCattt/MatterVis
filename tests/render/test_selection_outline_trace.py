from __future__ import annotations

from crystal_viewer.render.selection import selection_outline_trace


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
