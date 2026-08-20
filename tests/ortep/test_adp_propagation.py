from __future__ import annotations

import numpy as np

from mat_viewer.scene import build_scene_from_atoms, scene_ops


def test_adp_fields_survive_scene_build():
    ops = scene_ops()
    cell = type("Cell", (), {"a": 1, "b": 1, "c": 1, "alpha": 90, "beta": 90, "gamma": 90, "volume": 1})()
    atom = {
        "label": "C1",
        "elem": "C",
        "frac": np.array([0.0, 0.0, 0.0]),
        "cart": np.array([0.0, 0.0, 0.0]),
        "occ": 1.0,
        "dg": ".",
        "da": ".",
        "U": np.eye(3) * 0.04,
        "uiso": 0.04,
    }
    scene = build_scene_from_atoms(
        name="test",
        title="test",
        atoms=[atom],
        cell=cell,
        M=np.eye(3),
        R=np.eye(3),
        ops=ops,
        display_mode="cluster",
    )
    assert np.allclose(scene["draw_atoms"][0]["U"], atom["U"])
    assert scene["draw_atoms"][0]["uiso"] == 0.04
