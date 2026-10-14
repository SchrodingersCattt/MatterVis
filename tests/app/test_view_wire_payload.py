from __future__ import annotations

import json

from mat_viewer.app import ViewerBackend


def test_figure_broadcast_is_strict_json_and_drops_renderer_caches(tmp_path):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        scene_id = backend.active_scene_id()
        state = backend.get_state(scene_id)
        payload = backend.broadcast_figure(
            scene_id=scene_id,
            state=state,
            figure={
                "data": [{"type": "scatter3d", "x": [float("nan")], "y": [0], "z": [0]}],
                "layout": {"scene": {}},
            },
            topology_data={"coordination_number": 1, "_background_dict_cache": {(1, 2): []}},
        )

        assert "_background_dict_cache" not in payload["topology"]
        json.dumps(payload, allow_nan=False)
    finally:
        backend.close()
