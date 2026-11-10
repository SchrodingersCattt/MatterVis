"""The effect core does not import Dash, Plotly, or a GPU backend. T23."""

from __future__ import annotations

import subprocess
import sys


def test_effect_core_import_is_free_of_dash_plotly_and_gpu():
    script = (
        "import mat_viewer.effects, mat_viewer.effects.session, mat_viewer.effects.operations, sys\n"
        "forbidden = {'dash', 'plotly', 'wgpu'}\n"
        "loaded = forbidden & set(sys.modules)\n"
        "assert not loaded, sorted(loaded)\n"
    )
    subprocess.check_call([sys.executable, "-c", script])


def test_scene_for_state_does_not_write_bundle_scene():
    from pathlib import Path

    for relative in ("mat_viewer/app/backend_state.py", "mat_viewer/app/backend_core.py"):
        text = Path(relative).read_text(encoding="utf-8")
        assert "bundle.scene = scene" not in text
        assert "bundle.fragment_table = scene.get" not in text
