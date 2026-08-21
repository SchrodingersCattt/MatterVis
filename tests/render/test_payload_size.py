"""Guardrail tests for the figure payload that gets shipped on every UI
change. The interactive viewer feels "frozen" once any single round
trip exceeds a few hundred kilobytes -- we caught a regression where
the polyhedron-edge trace alone was 350 KB and dragged total update
times past 1 second on top of typical home / office bandwidth.

These tests fail-fast if a future renderer change blows up the wire
size again."""

from __future__ import annotations

import json

import pytest

from mat_viewer.renderer import _round_coord_arrays, _merged_hull_edges


def test_round_coord_arrays_quantises_floats():
    """The mesh-coordinate rounding step is the broadest payload-size
    optimisation we have -- a regression here would silently triple
    every Mesh3d / Scatter3d trace size."""
    trace = {"x": [0.123456789, 1.987654321], "y": [2.5, 3.0], "z": [0.0, 0.0]}
    out = _round_coord_arrays(trace)
    assert out["x"] == [0.123, 1.988]


def test_round_coord_arrays_casts_indices_to_int():
    """Numpy ``int`` arrays serialise as ``1.0`` via ``to_plotly_json``
    -- two extra characters per index. Force ``int`` so a 5000-triangle
    mesh saves ~30 KB on the wire."""
    trace = {"i": [0.0, 1.0, 2.0], "j": [3.0, 4.0, 5.0], "k": [6.0, 7.0, 8.0]}
    out = _round_coord_arrays(trace)
    assert out["i"] == [0, 1, 2]
    assert all(isinstance(v, int) for v in out["i"])


def test_round_coord_arrays_skips_non_numeric_values():
    """``text`` arrays for atom-label scatter traces must not be
    coerced to floats."""
    trace = {"x": [0.1234], "text": ["O1", "C7"]}
    out = _round_coord_arrays(trace)
    assert out["x"] == [0.123]
    assert out["text"] == ["O1", "C7"]


def test_merged_hull_edges_emits_scatter3d_lines():
    """Polyhedron edges must serialise as a single Scatter3d/lines
    trace, not a Mesh3d cylinder bundle. Cylinders look prettier but
    they were responsible for 60% of the figure JSON in DAP-4; lines
    look almost identical at typical zoom and ship in a tenth the
    size."""
    pytest.importorskip("scipy.spatial")
    overlays = [
        [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (1.0, 1.0, 1.0),
        ]
    ]
    traces = _merged_hull_edges(overlays, color="#7C5CBF")
    assert len(traces) == 1
    payload = traces[0].to_plotly_json()
    assert payload["type"] == "scatter3d"
    assert payload["mode"] == "lines"


def test_merged_hull_edges_payload_under_5kb_for_one_polyhedron():
    """A single convex polyhedron must serialise to well under 5 KB.
    A cylinder-mesh implementation regressed this trace type to 50+
    KB per polyhedron and 350 KB total once 40 polyhedra were tiled."""
    pytest.importorskip("scipy.spatial")
    overlays = [
        [
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (1.0, 1.0, 1.0),
        ]
    ]
    traces = _merged_hull_edges(overlays, color="#7C5CBF")
    payload = traces[0].to_plotly_json()
    size = len(json.dumps(payload))
    assert size < 5_000, f"hull-edge payload regressed to {size} bytes for 1 polyhedron"
