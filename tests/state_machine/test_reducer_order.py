from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


def test_reducer_rejects_out_of_order_client_sequence(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))

    backend.apply_intent(
        {
            "type": "set_style",
            "client_id": "client-a",
            "client_seq": 2,
            "payload": {"atom_scale": 1.2},
        }
    )

    with pytest.raises(Exception):
        backend.apply_intent(
            {
                "type": "set_style",
                "client_id": "client-a",
                "client_seq": 1,
                "payload": {"atom_scale": 1.1},
            }
        )
