from __future__ import annotations

import json
from pathlib import Path

from mat_viewer.app import ViewerBackend
from mat_viewer.presets import DEFAULT_STYLE


def test_restore_display_defaults_ignores_custom_preset_and_preserves_operations(
    tmp_path: Path,
) -> None:
    preset_path = tmp_path / "custom-preset.json"
    preset_path.write_text(
        json.dumps(
            {
                "style": {
                    "atom_scale": 2.75,
                    "show_labels": True,
                    "show_axes": False,
                },
                "structures": {
                    "DAP-4": {"style": {"bond_radius": 0.91}},
                },
            }
        ),
        encoding="utf-8",
    )
    backend = ViewerBackend(preset_path=str(preset_path), root_dir=str(tmp_path))
    try:
        scene_id = backend.active_scene_id()
        structure = backend.get_state(scene_id)["structure"]
        backend.patch_state(
            {
                "atom_scale": 3.0,
                "bond_groups": [{"id": "custom-bonds", "name": "custom"}],
                "display_options": ["labels"],
                "transforms": [],
                "camera": {
                    "eye": {"x": 0.1, "y": 0.2, "z": 0.3},
                    "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "up": {"x": 0.0, "y": 1.0, "z": 0.0},
                },
            },
            scene_id=scene_id,
            broadcast=False,
        )
        before = backend.get_state(scene_id)
        restored = backend.restore_display_defaults(scene_id=scene_id, broadcast=False)

        assert restored["structure"] == structure == before["structure"]
        assert restored["transforms"] == before["transforms"]
        assert restored["atom_scale"] == DEFAULT_STYLE["atom_scale"]
        assert restored["bond_radius"] == DEFAULT_STYLE["bond_radius"]
        assert restored["display_options"] != ["labels"]
        assert restored["bond_groups"] == []
        assert restored["camera"] != before["camera"]
    finally:
        backend.close()
