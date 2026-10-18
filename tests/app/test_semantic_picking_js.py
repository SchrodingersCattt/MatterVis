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
    assert 'postSelection(picked.rect, picked.viewport, false, false)' in body


def test_context_menu_coordinate_fallback_preserves_semantic_target_without_hover():
    body = SOURCE[SOURCE.index("function bindRightClick"):SOURCE.index("// ── Keyboard shortcuts")]
    assert "function targetFromSelectionResponse" in SOURCE
    assert "postSelection(picked.rect, picked.viewport, false, false)" in body
    assert "targetFromSelectionResponse(body, sceneId)" in body
    assert 'kind: payload ? payload.kind : "_global"' in body
    assert "const x = Number(e.clientX);" in body
    assert "const y = Number(e.clientY);" in body


def test_escape_clears_local_targets_and_render_tab_handoffs_invalidate_hover():
    shortcuts = SOURCE[SOURCE.index("// ── Keyboard shortcuts"):SOURCE.index("// ── Box selection")]
    assert 'publishSemanticSelection(null, "clear")' in shortcuts
    assert "MatterVisInvalidateSemanticHover" in shortcuts
    assert "function invalidateSemanticHover" in SOURCE
    assert "gd.__mvGraphGeneration=Number(gd.__mvGraphGeneration||0)+1" in SOURCE
    assert "window.MatterVisInvalidateSemanticHover();" in SOURCE


def test_shift_box_selection_is_pointer_safe_and_uses_plotly_viewport():
    body = SOURCE[SOURCE.index("// ── Box selection"):SOURCE.index("// ── Disorder hover")]
    assert "function begin(e)" in body
    assert "function finish(e, cancelled)" in body
    assert 'container.addEventListener("pointerdown", begin, true)' in body
    assert 'document.addEventListener("pointercancel"' in body
    assert 'source:"shift-drag"' in body
    assert "start.additive = true" in body
    assert 'container.querySelector(".js-plotly-plot")' in body
    assert "additive:additive" in body


def test_semantic_plotly_click_owns_left_click_before_coordinate_fallback():
    body = SOURCE[SOURCE.index("function bindRightClick"):SOURCE.index("// ── Keyboard shortcuts")]
    plotly_click = body.index('inner.on("plotly_click"')
    assert "function primaryPick" in body
    assert "pointerup" in body
    assert "gestureSequence" in body
    assert "semanticClickGesture" in body
    assert "pendingCoordinateGesture" in body
    assert "gestureGraceMs = 1000" in body
    assert "releaseSemanticClickGesture" in body
    assert "finishCoordinateGesture" in body
    assert "hasPendingCoordinateGesture" in body
    assert "postSelection(picked.rect, picked.viewport, false, false)" in body
    assert "requestAnimationFrame" in body
    assert "selection && selection.targets" in body
    assert "publishSemanticSelection(atomTarget, mode)" in body
    assert "setTimeout(function () {" in body
    assert "setTimeout(function () { plotlyClickObserved = false; }, 0);" in body
    assert "plotlyClickObserved || hasPendingCoordinateGesture() || modifier" in body
    assert "recentPointer && recentPointer.moved" in body
    assert "gestureSequence !== gestureId" in body
    assert "e.ctrlKey || e.metaKey || e.shiftKey" in body
    assert "clearSemanticClickGesture(semanticClickGesture)" in body
    assert "pointerDown && pointerDown.id === gestureId" in body
    assert "moved:moved > 4" in body
    assert "if (recentPointer && recentPointer.moved) return;" in body
    assert "ctrlKey:!!e.ctrlKey" in body
    assert "metaKey:!!e.metaKey" in body
    assert "shiftKey:!!e.shiftKey" in body
    assert plotly_click < body.index('container.addEventListener("contextmenu"')
