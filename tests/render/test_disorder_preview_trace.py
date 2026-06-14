from __future__ import annotations

from crystal_viewer.render.selection import disorder_preview_outline_trace


def _scene():
    return {
        "draw_atoms": [
            {"label": "A1", "cart": [0.0, 0.0, 0.0], "atom_radius": 0.2},
            {"label": "B1", "cart": [1.0, 0.0, 0.0], "atom_radius": 0.2},
        ]
    }


def test_disorder_preview_trace_keeps_empty_mesh_available_for_patching():
    trace = disorder_preview_outline_trace(_scene(), {"atom_scale": 1.0}, highlight_labels=set())
    payload = trace.to_plotly_json()

    assert payload["name"] == "disorder-preview-outline"
    assert payload["meta"]["mv_role"] == "disorder_preview"
    assert payload["x"] == []
    assert payload["visible"] is False


def test_disorder_preview_trace_emits_mesh_for_highlighted_labels():
    trace = disorder_preview_outline_trace(_scene(), {"atom_scale": 1.0}, highlight_labels={"A1"})
    payload = trace.to_plotly_json()

    assert payload["name"] == "disorder-preview-outline"
    assert len(payload["x"]) > 0
    assert payload["visible"] is True
