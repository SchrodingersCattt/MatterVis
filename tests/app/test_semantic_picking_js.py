"""Executable hit-test contracts; native gestures are covered by WebGL tests."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_local_scene_picker_geometry_occlusion_and_lifecycle():
    node = shutil.which("node")
    if not node:
        pytest.skip("node required for browser geometry contracts")
    result = subprocess.run([node, str(ROOT / "tests/app/scene_picking_contract.js"),
                             str(ROOT / "frontend/assets/scene_picking.js")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_hover_and_context_menu_have_no_server_coordinate_fallback():
    source = (ROOT / "frontend/assets/mattervis.js").read_text()
    controller = source[source.index("function bindRightClick"):source.index("// ── Keyboard shortcuts")]
    assert "fetch(" not in controller
    assert "postSelection(" not in controller
    assert '"plotly_click"' not in controller
    assert "__mvSemanticBound" in controller
    assert "pointercancel" in controller
    assert "gesture.scene!==currentSceneId()" in controller
    pointerup = controller[controller.index('addEventListener("pointerup"'):controller.index('addEventListener("pointercancel"')]
    assert "gesture.scene!==currentSceneId()" in pointerup
    assert "gesture.gd" not in pointerup
    assert "selection_gesture_ts" in controller
    assert "selectionGestureTsByScene" in controller
    select = controller[controller.index("function select("):controller.index('addEventListener("pointerup"')]
    assert 'mode==="replace"' in select
    assert "mv-status-busy" in select
    assert "mv-render-status" in select
    assert "FRAME_SETTLE_MS" in select
    assert "selectionAttemptCurrent(token, scene, generation)" in select
    assert "__mvGraphGeneration" not in select
    pointerdown = controller[controller.index('addEventListener("pointerdown"'):controller.index("function select(")]
    assert "generation:currentSceneGeneration()" in pointerdown
    assert "attempt:selectionAttemptToken" in pointerdown
    assert "select(e, gesture)" in pointerup


def test_appearance_undo_rejects_obsolete_target_revision():
    node = shutil.which("node")
    if not node:
        pytest.skip("node required for appearance contracts")
    result = subprocess.run(
        [node, str(ROOT / "tests/app/appearance_undo_contract.js"),
         str(ROOT / "frontend/assets/selection_appearance.js")],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
