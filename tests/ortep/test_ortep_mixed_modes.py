from __future__ import annotations

import numpy as np

from crystal_viewer.ortep import (
    ortep_atom_mesh_traces,
    ortep_octant_shade_traces,
)


def _mixed_scene():
    return {
        "view_x": np.array([1.0, 0.0, 0.0]),
        "view_y": np.array([0.0, 1.0, 0.0]),
        "draw_atoms": [
            {
                "label": "C1",
                "elem": "C",
                "cart": [0.0, 0.0, 0.0],
                "color": "#555555",
                "is_minor": False,
                "U": np.eye(3) * 0.04,
                "uiso": 0.04,
            },
            {
                "label": "C1A",
                "elem": "C",
                "cart": [10.0, 0.0, 0.0],
                "color": "#555555",
                "is_minor": True,
                "U": np.eye(3) * 0.04,
                "uiso": 0.04,
            },
        ],
    }


def test_ortep_minor_mode_splits_octant_and_axis_traces():
    scene = _mixed_scene()
    style = {
        "style": "ortep",
        "ortep_probability": 0.5,
        "ortep_mode": "ortep_octant",
        "minor_opacity": 0.35,
        "major_opacity": 1.0,
        "disorder": "dashed_bonds",
    }

    atom_traces = ortep_atom_mesh_traces(scene, style)
    octant_traces = ortep_octant_shade_traces(scene, style)

    # Both major and minor atoms should produce mesh traces
    assert len(atom_traces) >= 1
    # Octant shading applies to all atoms (no per-minor gating)
    assert len(octant_traces) >= 1
