"""Browser camera callbacks that must not live in the view registrar.

``camera-state-store`` is written only with ``allow_duplicate``. Reading
it from here is what records the pose the server accepted. A primary
writer beside those duplicates drops user-event dispatch on Dash 2.18.
"""

from __future__ import annotations

from dash import Input, Output


def register_camera_clientside(app) -> None:
    """Register the relayout capture and the accepted-camera book."""
    if not hasattr(app, "clientside_callback"):
        return
    app.clientside_callback(
        """
        function(relayout) {
          if (!relayout || typeof relayout !== "object") return window.dash_clientside.no_update;
          var keys = Object.keys(relayout);
          var touches = false;
          for (var i = 0; i < keys.length; i++) {
            var key = keys[i];
            if (key === "scene.camera" || key.indexOf("scene.camera.") === 0) touches = true;
            if (key === "scene" && relayout.scene && relayout.scene.camera) touches = true;
          }
          if (!touches) return window.dash_clientside.no_update;
          var gd = document.querySelector("#crystal-graph .js-plotly-plot");
          var meta = gd && gd.layout && gd.layout.meta && gd.layout.meta.mattervis_render;
          var canvasId = meta && meta.scene_id ? String(meta.scene_id) : "";
          var gestureId = window.__mvGestureSceneId ? String(window.__mvGestureSceneId) : "";
          var gestureKind = window.__mvGestureKind ? String(window.__mvGestureKind) : "";
          // Dash may deliver relayoutData after Plotly has already
          // painted the next scene and after the pointer gesture ended.
          // Keep the scene that owned the last pointer gesture as a
          // second fence so a late camera payload from scene A cannot be
          // reinterpreted as a drag on scene B. A new pointerdown refreshes
          // this marker for legitimate drags on B.
          var lastGestureId = window.__mvLastGestureSceneId
            ? String(window.__mvLastGestureSceneId) : "";
          if (gestureId && canvasId && gestureId !== canvasId && gestureKind !== "wheel") return window.dash_clientside.no_update;
          // A wheel timeout clears the active gesture id and kind together
          // but keeps the last scene fence. An empty kind still fails the
          // wheel exception, so a late relayout cannot move onto the next
          // canvas.
          if (!gestureId && lastGestureId && canvasId && lastGestureId !== canvasId && gestureKind !== "wheel") {
            return window.dash_clientside.no_update;
          }
          var sceneId = (gestureKind === "wheel" && canvasId) ? canvasId : (gestureId || canvasId);
          if (!sceneId) return window.dash_clientside.no_update;
          return {
            scene_id: sceneId,
            scene_generation: window.__mvGestureSceneGeneration,
            camera_revision: window.__mvGestureCameraRevision,
            relayout: relayout,
            seq: Date.now(),
          };
        }
        """,
        Output("camera-capture-store", "data"),
        Input("crystal-graph", "relayoutData"),
        prevent_initial_call=True,
    )
    app.clientside_callback(
        """
        function(data) {
          if (!data || !data.scene_id || !data.camera) return window.dash_clientside.no_update;
          var id = String(data.scene_id);
          var book = window.__mvServerCameraByScene || (window.__mvServerCameraByScene = {});
          var previous = book[id];
          var revision = data.camera_revision;
          if (previous && revision !== undefined && revision !== null &&
              Number(revision) < Number(previous.camera_revision || 0)) {
            return window.dash_clientside.no_update;
          }
          book[id] = {
            camera: data.camera,
            camera_revision: revision === undefined || revision === null
              ? (previous ? previous.camera_revision : 0)
              : Number(revision),
            scene_generation: data.scene_generation !== undefined && data.scene_generation !== null
              ? data.scene_generation
              : (previous ? previous.scene_generation : null)
          };
          if (typeof window.__mvApplyServerCamera === "function") {
            window.__mvApplyServerCamera(id);
          }
          return window.dash_clientside.no_update;
        }
        """,
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("camera-state-store", "data"),
        prevent_initial_call=True,
    )
