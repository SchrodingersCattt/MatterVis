from __future__ import annotations
# ruff: noqa: F401,F403,F405

import copy

from .shared import *
from .camera_helpers import *
from .style_helpers import *
from .normalizers import *
from .editor_tables import *
from .editor_transforms import *
from .rightclick import *
from ..cache_keys import topology_side_panel_cache_key
from .view_updates import UpdateKind, classify_change, make_update
from .backend import ViewerBackend


def client_http_frame(backend, cursor, scene_id):
    """Return the selected scene's newest frame once for this cursor.

    The cursor is browser state, keyed by scene id. One client's sequence
    cannot hide a frame from another client, and a higher sequence on scene
    A cannot hide an older sequence that still belongs to scene B.
    """
    stored = dict(cursor) if isinstance(cursor, dict) else {}
    if not scene_id:
        return None, stored
    event = backend.latest_figure_broadcast(str(scene_id))
    if not event:
        return None, stored
    seq = int(event.get("figure_seq") or 0)
    key = str(scene_id)
    if int(stored.get(key) or 0) >= seq:
        return None, stored
    stored[key] = seq
    return event, stored


def register_view_callbacks(app, backend):
    # Throttle high-frequency drag relayout frames so camera persistence
    # does not bump backend version on every mouse-move.
    _last_camera_commit_by_scene: dict[str, float] = {}
    _camera_commit_min_interval_s = 0.25

    # Every confirmed state mutation gets one typed envelope.  The browser
    # clientside callback below is the sole Plotly/SVG submitter for these
    # envelopes; Dash never competes with the WebSocket frame path.
    _last_confirmed_state: dict[str, Any] = {}

    @app.callback(
        Output("view-update-store", "data", allow_duplicate=True),
        Input("agent-state-store", "data"),
        prevent_initial_call=True,
    )
    def publish_confirmed_state(state):
        if not isinstance(state, dict):
            return no_update
        scene_id = str(state.get("scene_id") or "")
        before = _last_confirmed_state.get(scene_id)
        _last_confirmed_state[scene_id] = copy.deepcopy(state)
        payload = {
            "display_options": list(state.get("display_options") or []),
            "axis_scale": state.get("axis_scale"),
            "minor_opacity": state.get("minor_opacity"),
            "polyhedron_specs": copy.deepcopy(state.get("polyhedron_specs") or []),
            "label_mode": state.get("label_mode"),
        }
        if before is None:
            # The first confirmed edit of a scene is still a real edit.
            # prevent_initial_call means this callback never saw the seed
            # state, so classify from the kind the reducer just recorded.
            kind = str(getattr(backend, "_last_update_kind_by_scene", {}).get(scene_id) or "")
            if not kind:
                return no_update
            if kind == UpdateKind.CAMERA.value:
                payload["camera"] = copy.deepcopy(state.get("camera"))
                payload["command"] = "state-confirmed"
            return make_update(
                state, operation=kind, payload=payload, reason="state-confirmed"
            ).to_dict()
        update = make_update(
            state, before=before, payload=payload, reason="state-confirmed"
        )
        if getattr(update.kind, "value", update.kind) == UpdateKind.CAMERA.value:
            update = make_update(
                state,
                before=before,
                payload={**payload, "camera": copy.deepcopy(state.get("camera")), "command": "state-confirmed"},
                reason="state-confirmed",
            )
        return update.to_dict()

    if hasattr(app, "clientside_callback"):
        app.clientside_callback(
            """
            function(update) {
              if (window.MatterVisViewUpdates &&
                  typeof window.MatterVisViewUpdates.submit === 'function') {
                return window.MatterVisViewUpdates.submit(update) || '';
              }
              return '';
            }
            """,
            Output("view-update-ack", "children", allow_duplicate=True),
            Input("view-update-store", "data"),
            prevent_initial_call=True,
        )

    @app.callback(
        Output("view-update-store", "data", allow_duplicate=True),
        Output("view-http-cursor", "data"),
        Input("agent-state-poll", "n_intervals"),
        State("view-http-cursor", "data"),
        State("agent-state-store", "data"),
        prevent_initial_call=True,
    )
    def deliver_http_fallback(_n_intervals, cursor, state):
        """Deliver one scene's newest frame to this browser's own cursor."""
        scene_id = state.get("scene_id") if isinstance(state, dict) else None
        event, stored = client_http_frame(backend, cursor, scene_id)
        if event is None:
            return no_update, no_update
        return event, stored

    @app.callback(
        Output("rightclick-target", "data", allow_duplicate=True),
        Input("rightclick-target-fallback", "value"),
        prevent_initial_call=True,
    )
    def sync_rightclick_fallback(raw_value):
        if not raw_value:
            return no_update
        try:
            import json as _json
            payload = _json.loads(raw_value)
            return payload
        except (ValueError, TypeError):
            return no_update

    @app.callback(
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("scene-selection-store", "data"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def commit_scene_target_selection(selection, active_scene_id):
        """Persist semantic browser picks without routing pointer motion through Dash."""

        if not isinstance(selection, dict) or not isinstance(selection.get("targets"), list):
            return no_update
        scene_id = str(selection.get("scene_id") or active_scene_id or backend.active_scene_id())
        mode = str(selection.get("mode") or "replace")
        if mode == "clear":
            backend.clear_target_selection(scene_id=scene_id)
        else:
            # The browser store carries the complete post-click target list.
            # Applying ``toggle`` to that whole list would toggle every
            # already-selected item a second time, so treat it as the
            # authoritative snapshot and replace the server list.  This is
            # especially important for Ctrl/Cmd clicks that remove one item.
            backend.set_target_selection(
                selection.get("targets") or [],
                scene_id=scene_id,
            )
        return backend.get_state(scene_id)

    @app.callback(
        Output("rightclick-menu", "children"),
        Output("rightclick-menu", "style"),
        Output("rightclick-menu", "className"),
        Input("rightclick-target", "data"),
    )
    def render_rightclick_menu(target):
        from dash import dcc, html

        hidden_class = "rightclick-menu rightclick-menu--hidden"
        empty_style = {"top": "0px", "left": "0px"}
        if not target or not isinstance(target, dict):
            return _closed_rightclick_items(), empty_style, hidden_class
        kind = target.get("kind")
        if kind == "_close":
            return _closed_rightclick_items(), empty_style, hidden_class
        # Keyboard-shortcut path: just dispatch and don't render. We
        # still want the popover hidden (it might have been visible
        # before).
        if target.get("action"):
            return _closed_rightclick_items(), empty_style, hidden_class
        payload = target.get("payload") or {}
        x = int(target.get("x") or 0)
        y = int(target.get("y") or 0)
        items: list[Any] = []
        header_text = ""
        color_picker_color = "#888888"

        if kind == "atom":
            label = payload.get("label") or "(atom)"
            elem = payload.get("element") or ""
            header_text = f"Atom \u00b7 {label} ({elem})"
            items.extend([
                html.Button("Select atom", id="rcm-action-select", n_clicks=0, className="rightclick-menu__item"),
                html.Button("Add to selection", id="rcm-action-select-add", n_clicks=0, className="rightclick-menu__item"),
                html.Button("Select fragment", id="rcm-action-select-fragment", n_clicks=0, className="rightclick-menu__item"),
                html.Button(f"Select all {elem}", id="rcm-action-select-element", n_clicks=0, className="rightclick-menu__item"),
                html.Div(className="rightclick-menu__divider"),
                html.Div(
                    [
                        html.Label("Colour", htmlFor="rcm-color-picker"),
                        dcc.Input(
                            id="rcm-color-picker",
                            type="color",
                            value=color_picker_color,
                            debounce=False,
                        ),
                    ],
                    className="rightclick-menu__color",
                ),
                html.Button(
                    [html.Span("Hide this atom"), html.Span("h", className="rightclick-menu__shortcut")],
                    id="rcm-action-hide",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    [html.Span("Grow by 1 bond hop"), html.Span("g", className="rightclick-menu__shortcut")],
                    id="rcm-action-grow-bonds",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    [html.Span("Grow by 4\u202f\u00c5 radius"), html.Span("\u21e7g", className="rightclick-menu__shortcut")],
                    id="rcm-action-grow-radius",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Complete fragment",
                    id="rcm-action-complete-fragment",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Analyze coordination",
                    id="rcm-action-analyze",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Div(className="rightclick-menu__divider"),
                html.Button(
                    [html.Span("Promote to group rule"), html.Span("p", className="rightclick-menu__shortcut")],
                    id="rcm-action-promote",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button("Promote selection to group", id="rcm-action-selection-promote", n_clicks=0, className="rightclick-menu__item"),
            ])
        elif kind == "polyhedron":
            label = payload.get("fragment_label") or "(polyhedron)"
            header_text = f"Polyhedron \u00b7 {label}"
            items.extend([
                html.Div(
                    [
                        html.Label("Colour", htmlFor="rcm-color-picker"),
                        dcc.Input(
                            id="rcm-color-picker",
                            type="color",
                            value=color_picker_color,
                            debounce=False,
                        ),
                    ],
                    className="rightclick-menu__color",
                ),
                html.Button(
                    [html.Span("Hide this polyhedron"), html.Span("h", className="rightclick-menu__shortcut")],
                    id="rcm-action-hide",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Complete coordination",
                    id="rcm-action-complete-fragment",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Div(className="rightclick-menu__divider"),
                # Keep the rest of the popover schema consistent with
                # the atom branch so the buttons exist for the
                # callback's Inputs (Dash needs the ids present).
                html.Button(
                    "Grow polyhedron neighbourhood",
                    id="rcm-action-grow-bonds",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Grow by 4\u202f\u00c5 radius",
                    id="rcm-action-grow-radius",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Re-analyze",
                    id="rcm-action-analyze",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Promote to group rule",
                    id="rcm-action-promote",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button("", id="rcm-action-select", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-select-add", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-select-fragment", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-select-element", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-selection-promote", n_clicks=0, style={"display": "none"}),
            ])
        elif kind == "bond":
            label = payload.get("label_pair") or "(bond)"
            elements = payload.get("element_pair") or ""
            header_text = f"Bond \u00b7 {label} ({elements})"
            items.extend([
                html.Div(
                    [
                        html.Label("Colour", htmlFor="rcm-color-picker"),
                        dcc.Input(
                            id="rcm-color-picker",
                            type="color",
                            value=color_picker_color,
                            debounce=False,
                        ),
                    ],
                    className="rightclick-menu__color",
                ),
                html.Button(
                    [html.Span("Hide bonds like this"), html.Span("h", className="rightclick-menu__shortcut")],
                    id="rcm-action-hide",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Button(
                    "Promote to bond-group rule",
                    id="rcm-action-promote",
                    n_clicks=0,
                    className="rightclick-menu__item",
                ),
                html.Div(className="rightclick-menu__divider"),
                # Hidden no-ops to satisfy callback Input list.
                html.Button("", id="rcm-action-grow-bonds", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-grow-radius", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-complete-fragment", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-analyze", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-select", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-select-add", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-select-fragment", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-select-element", n_clicks=0, style={"display": "none"}),
                html.Button("", id="rcm-action-selection-promote", n_clicks=0, style={"display": "none"}),
            ])
        else:
            return _closed_rightclick_items(), empty_style, hidden_class

        children: list[Any] = [html.Div(header_text, className="rightclick-menu__header")] + items
        # Position: clamp so the menu stays inside the viewport. The
        # JS sends viewport coords (clientX/clientY); we use position:
        # fixed in CSS so the same coords work directly.
        style = {
            "top": f"{max(8, y)}px",
            "left": f"{max(8, x)}px",
        }
        return children, style, "rightclick-menu"

    @app.callback(
        Output("agent-state-store", "data", allow_duplicate=True),
        Output("rightclick-target", "data", allow_duplicate=True),
        Input("rcm-action-hide", "n_clicks"),
        Input("rcm-action-grow-bonds", "n_clicks"),
        Input("rcm-action-grow-radius", "n_clicks"),
        Input("rcm-action-complete-fragment", "n_clicks"),
        Input("rcm-action-analyze", "n_clicks"),
        Input("rcm-action-promote", "n_clicks"),
        Input("rcm-action-select", "n_clicks"),
        Input("rcm-action-select-add", "n_clicks"),
        Input("rcm-action-select-fragment", "n_clicks"),
        Input("rcm-action-select-element", "n_clicks"),
        Input("rcm-action-selection-promote", "n_clicks"),
        Input("rightclick-target", "data"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_rightclick_action(
        hide_clicks,
        grow_bonds_clicks,
        grow_radius_clicks,
        complete_clicks,
        analyze_clicks,
        promote_clicks,
        select_clicks,
        select_add_clicks,
        select_fragment_clicks,
        select_element_clicks,
        selection_promote_clicks,
        target,
        active_scene_id,
    ):
        triggered = getattr(callback_context, "triggered_id", None)
        if not target or not isinstance(target, dict):
            return no_update, no_update
        scene_id = active_scene_id or backend.active_scene_id()
        kind = target.get("kind")
        payload = target.get("payload") or {}
        # Keyboard path: store update with ``action`` set; do not also
        # consume button clicks on the same event.
        action = None
        if triggered == "rightclick-target":
            action = target.get("action")
            if not action:
                return no_update, no_update
        else:
            # Replacing menu children inserts buttons with n_clicks=0;
            # insertion is not a user action, even after initial page load.
            if not any(
                item.get("value")
                for item in callback_context.triggered
                if item.get("prop_id") == f"{triggered}.n_clicks"
            ):
                return no_update, no_update
            mapping = {
                "rcm-action-hide": "hide",
                "rcm-action-grow-bonds": "grow_bonds",
                "rcm-action-grow-radius": "grow_radius",
                "rcm-action-complete-fragment": "complete_fragment",
                "rcm-action-analyze": "analyze",
                "rcm-action-promote": "promote_to_group",
                "rcm-action-select": "select",
                "rcm-action-select-add": "select_add",
                "rcm-action-select-fragment": "select_fragment",
                "rcm-action-select-element": "select_element",
                "rcm-action-selection-promote": "selection_to_group",
            }
            action = mapping.get(triggered)
            if action is None:
                return no_update, no_update

        try:
            _dispatch_rightclick_action(backend, scene_id, action, kind, payload, target)
        except Exception:  # pragma: no cover - best-effort; surface in browser console
            return no_update, {"kind": "_close", "ts": time.time()}
        # Close the popover after a successful action.
        return backend.get_state(), {"kind": "_close", "ts": time.time()}

    @app.callback(
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("rcm-color-picker", "value"),
        State("rightclick-target", "data"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_rightclick_color(color, target, active_scene_id):
        if not color or not target or not isinstance(target, dict):
            return no_update
        scene_id = active_scene_id or backend.active_scene_id()
        kind = target.get("kind")
        payload = target.get("payload") or {}
        try:
            _dispatch_rightclick_action(
                backend, scene_id, "set_color", kind, payload, target, color=str(color)
            )
        except Exception:
            return no_update
        return backend.get_state()

    @app.callback(
        Output("kbd-help", "className"),
        Input("kbd-help-close", "n_clicks"),
        prevent_initial_call=True,
    )
    def close_kbd_help(_):
        return "kbd-help kbd-help--hidden"

    # ------------------------------------------------------------------
    # View tools (Phase 4): VESTA-style axis alignment + projection
    # toggle.
    #
    # Both callbacks call into ``backend.camera_action`` (the same path
    # exercised by ``POST /api/v2/camera/action``), then push the new
    # camera into ``camera-state-store`` and patch the Plotly layout
    # directly. The browser-side fast path usually does the same relayout
    # first, but the Dash Patch is the correctness fallback that prevents
    # the SVG compass from updating while the WebGL scene keeps the old
    # camera.
    # ------------------------------------------------------------------
    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Output("view-update-store", "data", allow_duplicate=True),
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("view-align-a", "n_clicks"),
        Input("view-align-b", "n_clicks"),
        Input("view-align-c", "n_clicks"),
        Input("view-align-astar", "n_clicks"),
        Input("view-align-bstar", "n_clicks"),
        Input("view-align-cstar", "n_clicks"),
        Input("view-reset", "n_clicks"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_view_action(_a, _b, _c, _astar, _bstar, _cstar, _reset, scene_id):
        triggered = getattr(callback_context, "triggered_id", None)
        if not triggered:
            return no_update, no_update, no_update
        scene_id = scene_id or backend.active_scene_id()
        if scene_id and scene_id not in backend.scene_store.scenes:
            return no_update, no_update, no_update
        button_to_axis = {
            "view-align-a": "a",
            "view-align-b": "b",
            "view-align-c": "c",
            "view-align-astar": "a*",
            "view-align-bstar": "b*",
            "view-align-cstar": "c*",
        }
        try:
            if triggered == "view-reset":
                camera = backend.camera_action("reset", scene_id=scene_id, broadcast=False)
            elif triggered in button_to_axis:
                camera = backend.camera_action(
                    "align",
                    scene_id=scene_id,
                    broadcast=False,
                    axis=button_to_axis[triggered],
                )
            else:
                return no_update, no_update, no_update
        except Exception:  # pragma: no cover - best-effort, surface in console
            return no_update, no_update, no_update
        state = backend.get_state(scene_id)
        camera_payload = _camera_store_payload(scene_id, camera)
        update = make_update(
            state,
            operation="camera",
            payload={"camera": camera, "command": triggered},
            reason="camera-command",
        ).to_dict()
        return (
            camera_payload,
            update,
            _fast_view_metadata(backend, state, camera_payload),
        )

    @app.callback(
        Output("view-projection", "value", allow_duplicate=True),
        Input("agent-state-store", "data"),
        prevent_initial_call=True,
    )
    def sync_view_projection_from_state(state):
        # Mirror the live scene projection onto the radio. The store
        # payload can be a scene switch that was already in flight when
        # the user picked Orthographic; writing that older value back
        # would undo the click.
        if not isinstance(state, dict):
            return no_update
        scene_id = state.get("scene_id")
        live = state
        if scene_id and scene_id in backend.scene_store.scenes:
            live = backend.get_state(scene_id)
        return _coerce_projection(live.get("projection") or "perspective")

    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Output("view-update-store", "data", allow_duplicate=True),
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("view-projection", "value"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def apply_view_projection(projection, scene_id):
        if not projection:
            return no_update, no_update, no_update
        scene_id = scene_id or backend.active_scene_id()
        if scene_id and scene_id not in backend.scene_store.scenes:
            return no_update, no_update, no_update
        # Skip the redraw if the user clicked the radio that was
        # already selected -- avoids ratcheting the figure JSON cache
        # for a no-op.
        current = backend.get_state(scene_id).get("projection", "perspective")
        if str(projection) == str(current):
            return no_update, no_update, no_update
        try:
            camera = backend.set_projection(projection, scene_id=scene_id, broadcast=False)
        except Exception:  # pragma: no cover
            return no_update, no_update, no_update
        state = backend.get_state(scene_id)
        camera_payload = _camera_store_payload(scene_id, camera)
        update = make_update(
            state,
            operation="camera",
            payload={"camera": camera, "command": "projection"},
            reason="projection-command",
        ).to_dict()
        return (
            camera_payload,
            update,
            _fast_view_metadata(backend, state, camera_payload),
        )

    if hasattr(app, "clientside_callback"):
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

    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Output("agent-state-store", "data", allow_duplicate=True),
        Output("view-update-store", "data", allow_duplicate=True),
        Input("camera-capture-store", "data"),
        State("camera-state-store", "data"),
        prevent_initial_call=True,
    )
    def capture_camera(capture, camera_state):
        # The stamp is the scene under the pointer when the drag started.
        # ``scene-tabs`` can already be the destination tab.
        scene_key = str((capture or {}).get("scene_id") or "")
        now = time.monotonic()
        result = _persist_relayout_camera(
            backend,
            capture,
            camera_state,
            now=now,
            last_commit=_last_camera_commit_by_scene.get(scene_key, 0.0),
            min_interval_s=_camera_commit_min_interval_s,
        )
        if result is None:
            return no_update, no_update, no_update
        _last_camera_commit_by_scene[result["scene_id"]] = now
        # A drag Plotly already applied can leave the structure outside the
        # orthographic window. Push the replacement back; a drag that still
        # shows an atom does not.
        view_update = result.get("view_update")
        return (
            result["camera_store"],
            result["agent_state"] if result["agent_state"] is not None else no_update,
            view_update if view_update is not None else no_update,
        )

    @app.callback(
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("agent-state-store", "data"),
        State("camera-state-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_fast_view_metadata(agent_state, camera_state):
        # The store is a notification, not a second editable state owner.
        # get_state supplies the authoritative render revision as well.
        state = backend.get_state((agent_state or {}).get("scene_id"))
        return _fast_view_metadata(backend, state, camera_state)

    @app.callback(
        Output("minor-opacity-slider", "disabled"),
        Output("minor-opacity-control", "style"),
        Input("disorder-selector", "value"),
    )
    def gate_minor_opacity(disorder):
        return _minor_opacity_disabled(disorder), _minor_opacity_control_style(disorder)

    @app.callback(
        Output("polyhedra-controls", "style"),
        Input("topology-toggle", "value"),
    )
    def gate_polyhedra_controls(topology_toggle):
        return _polyhedra_controls_style("enabled" in (topology_toggle or []))

    # ------------------------------------------------------------------
    # Perf-log panel
    #
    # Polls the in-process ``perf_log`` ring buffer every second and
    # appends new entries to the on-screen list. Each entry shows a
    # local-time clock, the callback / event label, the duration
    # (colour-coded), and a short payload summary (filename, atom
    # count, ...). The store keeps the latest sequence number so the
    # poll only ships new events.
    # ------------------------------------------------------------------
    @app.callback(
        Output("perf-log-panel", "className"),
        Input("perf-log-toggle", "n_clicks"),
        State("perf-log-panel", "className"),
        prevent_initial_call=True,
    )
    def toggle_perf_log(_, current_class):
        cls = current_class or "perf-log-panel perf-log-panel--collapsed"
        if "perf-log-panel--collapsed" in cls:
            return "perf-log-panel perf-log-panel--expanded"
        return "perf-log-panel perf-log-panel--collapsed"

    @app.callback(
        Output("perf-log-cursor", "data", allow_duplicate=True),
        Input("perf-log-clear", "n_clicks"),
        prevent_initial_call=True,
    )
    def clear_perf_log(_):
        perf_log.clear()
        return {"seq": perf_log.latest_seq(), "events": []}

    @app.callback(
        Output("perf-log-body", "children"),
        Output("perf-log-cursor", "data"),
        Input("perf-log-poll", "n_intervals"),
        State("perf-log-cursor", "data"),
    )
    def refresh_perf_log(_, cursor):
        cursor = cursor or {"seq": 0, "events": []}
        new_events = perf_log.recent(limit=200, since_seq=int(cursor.get("seq", 0)))
        if not new_events and cursor.get("events"):
            return no_update, no_update
        merged = list(cursor.get("events") or []) + new_events
        # Keep only the latest 80 entries on screen so the DOM stays
        # cheap; the full ring buffer is still available via
        # ``GET /api/v1/perf``.
        merged = merged[-80:]
        rows = [_perf_log_row(entry) for entry in reversed(merged)]
        latest = merged[-1]["seq"] if merged else int(cursor.get("seq", 0))
        return rows, {"seq": latest, "events": merged}

    @app.callback(
        Output("crystal-graph", "figure"),
        Output("topology-histogram", "figure"),
        Output("topology-results", "children"),
        Output("structure-summary", "children"),
        Input("agent-state-store", "data"),
        State("crystal-graph", "figure"),
        State("camera-state-store", "data"),
    )
    def update_view(agent_state, *args):
        # Current contract is (state, current_figure, camera_state).  Accept
        # the pre-refactor four-argument call shape for host integrations that
        # still invoke the callback directly in tests.
        if len(args) == 2:
            current_figure, _camera_state = args
        elif len(args) >= 3:
            _, current_figure, _camera_state = args[:3]
        else:
            current_figure, _camera_state = None, None
        # State notifications enqueue a full worker build. The existing poll
        # delivers completed frames when WS is absent; neither path assembles
        # molecular geometry synchronously in this callback.
        #
        # ``graph-interaction-store`` is intentionally NOT an Input.
        # Its sole purpose is gate deferred WS figure pushes in
        # ``mattervis.js``.  Listing it here would trigger
        # ``update_view`` on every pointerdown/wheel/pointerup,
        # tripping ``dcc.Loading``'s spinner every time the user
        # drags or zooms.  The WS fast lane already decouples async
        # pushes from live interaction in JS; no server-side gate is
        # needed.
        cb_start = time.monotonic()
        # ``no_update`` is truthy. A style capture that did not change state
        # must not be treated as a scene payload.
        if agent_state is not None and not isinstance(agent_state, dict):
            return (no_update,) * 4
        state = backend.get_state((agent_state or {}).get("scene_id"))
        scene_id = state.get("scene_id")
        previous_state = getattr(update_view, "_last_state", None)
        change_kind = classify_change(previous_state, state)
        if previous_state is None:
            try:
                change_kind = UpdateKind(
                    backend._last_update_kind_by_scene.get(str(scene_id), change_kind.value)
                )
            except (AttributeError, ValueError):
                pass
        update_view._last_state = copy.deepcopy(state)
        if change_kind in {
            UpdateKind.CAMERA,
            UpdateKind.OVERLAY,
            UpdateKind.DISPLAY,
        }:
            # Labels, opacity, polyhedron visibility and Axes are delivered
            # by the clientside submitter.  No worker and no full figure
            # response is needed for these changes.
            if (
                change_kind == UpdateKind.CAMERA
                and state.get("material") == "flat"
                and state.get("style") == "ortep"
            ):
                # Flat+ORTEP bakes the camera basis into a PNG.  Queue a
                # worker rebuild for this one path; ordinary Plotly cameras
                # continue using the client-side relayout fast path.
                backend._render_worker.request_figure_build(state, force=True)
            return (no_update,) * 4
        topo_key_preview = topology_side_panel_cache_key(state)
        prev_key = getattr(update_view, "_topo_cache_key", None)
        # One scheduling owner; no figure assembly on a Dash request thread.
        # The existing broadcast journal is also the HTTP fallback when WS is
        # absent. Never stamp a newer revision onto the browser's old geometry.
        if not backend._figure_revision_matches_current(scene_id, state):
            return (no_update,) * 4
        event = backend.latest_figure_broadcast(scene_id)
        if (
            not event
            or event.get("scene_id") != scene_id
            or event.get("render_revision") != state.get("render_revision")
            or (event.get("state") or {}).get("camera_revision", 0)
            != state.get("camera_revision", 0)
        ):
            backend._render_worker.request_figure_build(state)
            return (no_update,) * 4
        fig, topology_data = event["figure"], event.get("topology")
        # Dash's figure prop may lag behind a WS delivery; the browser gate
        # deduplicates that case after successful Plotly completion.
        current_layout = (current_figure or {}).get("layout") or {}
        layout = fig.get("layout") or {}
        if (
            (current_layout.get("meta") or {}).get("mattervis_render")
            == (layout.get("meta") or {}).get("mattervis_render")
            and (current_layout.get("scene") or {}).get("uirevision")
            == (layout.get("scene") or {}).get("uirevision")
        ):
            fig = no_update

        # The right-hand sidebar only changes when the *topology* state
        # or the chosen scene changes. Keep a memo on the callback
        # itself so toggling Labels / Axes / Atom Scale -- which all
        # leave the topology untouched -- skips serialising the
        # histogram + markdown + structure summary every time. Each of
        # these is only ~1-3 kB but they re-render on the client, and
        # the markdown table tear-down was visible in the CPU profile.
        topo_key = topo_key_preview
        if prev_key == topo_key:
            summary = _structure_summary(backend.get_bundle(state["structure"]).scene)
            perf_log.record(
                "callback:update_view",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "scene_id": state.get("scene_id"),
                    "side_panel": "cached",
                },
            )
            update_view._last_rendered_scene_id = state.get("scene_id")
            return fig, no_update, no_update, summary
        update_view._topo_cache_key = topo_key
        with perf_log.time_block("update_view:side_panel", kind="event"):
            summary = _structure_summary(backend.scene_for_state(state))
            histogram = topology_histogram_figure(topology_data)
            md = topology_results_markdown(topology_data)
        perf_log.record(
            "callback:update_view",
            duration_ms=(time.monotonic() - cb_start) * 1000.0,
            kind="cb",
            info={"scene_id": state.get("scene_id"), "side_panel": "rebuilt"},
        )
        update_view._last_rendered_scene_id = state.get("scene_id")
        return fig, histogram, md, summary

    @app.callback(
        Output("status-banner", "children"),
        Output("status-banner", "className"),
        Output("export-download", "data"),
        Output("cif-download", "data"),
        Output("status-dismiss-timer", "disabled"),
        Output("status-dismiss-timer", "n_intervals"),
        Input("save-preset-btn", "n_clicks"),
        Input("export-btn", "n_clicks"),
        Input("export-cif-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def save_or_export(_, __, ___):
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        if triggered == "export-cif-btn":
            try:
                result = backend.export_cif(scene_id=backend.active_scene_id())
            except Exception as exc:
                message, class_name = _status_message(
                    f"CIF export failed: {exc}", "error"
                )
                return message, class_name, no_update, no_update, False, 0
            message, class_name = _status_message(
                f"CIF export ready: {result['filename']}", "success"
            )
            return (
                message,
                class_name,
                no_update,
                dcc.send_string(result["content"], result["filename"]),
                False,
                0,
            )
        if triggered == "export-btn":
            state = backend.get_state()
            scene_label = state.get("scene_label") or "mattervis"
            material = state.get("material", "mesh")
            render_style = state.get("style", "ball_stick")

            if material == "flat" and render_style == "ortep":
                # Flat + ORTEP: export as PDF via Matplotlib 2D renderer
                import io
                from mat_viewer.render.api import render as unified_render
                from mat_viewer.scene import scene_style as _ss
                scene = backend.scene_for_state(state)
                style = backend.style_for_state(state, scene=scene)
                result = unified_render(scene, style)
                buf = io.BytesIO()
                result.mpl_figure.savefig(buf, format="pdf", bbox_inches="tight")
                import matplotlib.pyplot as plt
                plt.close(result.mpl_figure)
                buf.seek(0)
                filename = f"{scene_label.replace(os.sep, '_')}_ortep.pdf"
                message, class_name = _status_message(f"Export ready: {filename}", "success")
                return message, class_name, dcc.send_bytes(buf.getvalue, filename), no_update, False, 0

            # Default: PNG export via Plotly
            from ..capabilities import resolve_requirements

            resolution = resolve_requirements("static-web-export")
            try:
                resolution.require()
                png = backend.render_current_png(backend.active_scene_id())
            except Exception as exc:
                message, class_name = _status_message(
                    f"Export failed: {exc}. Required install: "
                    f"{resolution.install_command}",
                    "error",
                )
                return message, class_name, no_update, no_update, False, 0
            filename = f"{scene_label.replace(os.sep, '_')}.png"
            message, class_name = _status_message(f"Export ready: {filename}", "success")
            return message, class_name, dcc.send_bytes(lambda buffer: buffer.write(png), filename), no_update, False, 0
        try:
            result = backend.save_preset()
        except Exception as exc:
            message, class_name = _status_message(
                f"Save preset failed: {exc}", "error"
            )
            return message, class_name, no_update, no_update, False, 0
        message, class_name = _status_message(f"Saved preset: {result['path']}", "success")
        return message, class_name, no_update, no_update, False, 0

    @app.callback(
        Output("status-banner", "children", allow_duplicate=True),
        Output("status-banner", "className", allow_duplicate=True),
        Output("status-dismiss-timer", "disabled", allow_duplicate=True),
        Input("status-dismiss-timer", "n_intervals"),
        prevent_initial_call=True,
    )
    def dismiss_status(n_intervals):
        if not n_intervals:
            return no_update, no_update, no_update
        return "", _status_class("idle"), True
