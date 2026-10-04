from __future__ import annotations

from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np

from mat_viewer.app.backend_camera import _plotly_camera
from mat_viewer.app.backend import ViewerBackend


def _scene() -> dict:
    return {
        "draw_atoms": [
            {"cart": np.array([0.0, 0.0, 0.0])},
            {"cart": np.array([1.0, 0.0, 0.0])},
        ],
        "bonds": [],
        "M": np.eye(3),
        "cell": SimpleNamespace(),
        "view_direction": np.array([0.0, 0.0, 1.0]),
        "up": np.array([0.0, 1.0, 0.0]),
    }


def test_flat_ortep_figure_has_camera_anchor_and_image(monkeypatch, tmp_path):
    seen: list[np.ndarray] = []

    def fake_render(scene, _style):
        seen.append(np.asarray(scene["view_direction"], dtype=float))
        return plt.figure()

    monkeypatch.setattr("mat_viewer.ortep.flat_render.render_ortep_flat", fake_render)
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        figure = backend._flat_ortep_figure(
            _scene(),
            {"uirevision": "flat-ortep"},
            camera=_plotly_camera(
                {"eye": {"x": 1.0, "y": 0.0, "z": 0.0}, "center": {"x": 0.0, "y": 0.0, "z": 0.0}, "up": {"x": 0.0, "y": 0.0, "z": 1.0}}
            ),
        )
        payload = figure.to_plotly_json()
    finally:
        backend.close()

    assert payload["layout"]["scene"]["dragmode"] == "orbit"
    assert payload["layout"]["scene"]["uirevision"] == "flat-ortep"
    assert any(trace.get("type") == "scatter3d" for trace in payload["data"])
    assert payload["layout"]["images"]
    assert seen and np.allclose(seen[0], [1.0, 0.0, 0.0])


def test_flat_ortep_cache_key_includes_camera(tmp_path):
    backend = ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path))
    try:
        state = backend.get_state()
        state.update({"material": "flat", "style": "ortep"})
        first = dict(state)
        second = dict(state)
        first["camera"] = {"eye": {"x": 1.0, "y": 0.0, "z": 0.0}}
        second["camera"] = {"eye": {"x": 0.0, "y": 1.0, "z": 0.0}}
        assert backend._figure_state_cache_key(first) != backend._figure_state_cache_key(second)
    finally:
        backend.close()
