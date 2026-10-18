from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "frontend/assets/mattervis.js").read_text(encoding="utf-8")


def test_frontend_uses_one_semantic_target_decoder_and_modifier_selection():
    assert "function targetFromPoint" in SOURCE
    assert "function publishSemanticSelection" in SOURCE
    assert 'setDashStore("scene-selection-store"' in SOURCE
    assert "persistentTargetsByScene" in SOURCE
    assert "sceneSelectionKey" in SOURCE
    assert "prunePersistentTargets" in SOURCE
    assert "function clearSelectionIfGraphBackground" in SOURCE
    assert "setTimeout(function ()" in SOURCE
    assert "clearSelectionIfGraphBackground();" in SOURCE
    assert "plotlyClickObserved" in SOURCE
    assert 'native.shiftKey' in SOURCE
    assert 'native.ctrlKey' in SOURCE
    assert "function hoverInspector" in SOURCE
    assert 'inspector.id = "mv-hover-inspector"' in SOURCE


def test_frontend_resolves_mesh_face_aliases_without_null_zero_fallback():
    assert "function meshFaceOrdinal" in SOURCE
    assert '"faceNumber", "faceIndex", "pointNumber", "pointIndex"' in SOURCE
    assert "if (raw === undefined || raw === null || raw === \"\") continue;" in SOURCE
    assert "function faceIdAt" in SOURCE


def test_context_menu_does_not_unconditionally_reuse_stale_hover():
    body = SOURCE[SOURCE.index("function bindRightClick"):SOURCE.index("// ── Keyboard shortcuts")]
    assert "gd._hoverdata" in body
    assert "Date.now() - lastHoverPoint.at" in body
    assert 'if (!lastHovered) return' not in body
    assert "targetFromPoint(hoverdata.points[0])" in body
    assert 'postSelection(picked.rect, picked.viewport, false)' in body


def test_semantic_plotly_click_owns_left_click_before_coordinate_fallback():
    body = SOURCE[SOURCE.index("function bindRightClick"):SOURCE.index("// ── Keyboard shortcuts")]
    plotly_click = body.index('inner.on("plotly_click"')
    assert "function primaryPick" in body
    assert "pointerup" in body
    assert "gestureSequence" in body
    assert "semanticClickGesture" in body
    assert "postSelection(picked.rect, picked.viewport, additive, false)" in body
    assert "publishSemanticSelection(target, mode)" in body
    assert "setTimeout(function () {" in body
    assert "setTimeout(function () { plotlyClickObserved = false; }, 0);" in body
    assert plotly_click < body.index('container.addEventListener("contextmenu"')
