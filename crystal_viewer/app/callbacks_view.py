from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .camera_helpers import *
from .style_helpers import *
from .normalizers import *
from .editor_tables import *
from .editor_transforms import *
from .rightclick import *
from ..transforms import transforms_cache_key
from .backend import ViewerBackend


def register_view_callbacks(app, backend):
    # Throttle high-frequency drag relayout frames so camera persistence
    # does not bump backend version on every mouse-move.
    _last_camera_commit_by_scene: dict[str, float] = {}
    _camera_commit_min_interval_s = 0.25

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
            return [], empty_style, hidden_class
        kind = target.get("kind")
        if kind == "_close":
            return [], empty_style, hidden_class
        # Keyboard-shortcut path: just dispatch and don't render. We
        # still want the popover hidden (it might have been visible
        # before).
        if target.get("action"):
            return [], empty_style, hidden_class
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
            return [], empty_style, hidden_class

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
        Output("crystal-graph", "figure", allow_duplicate=True),
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
        scene = backend.scene_for_state(state)
        style = backend.style_for_state(state, scene=scene)
        camera_payload = _camera_store_payload(scene_id, camera)
        topology_data = backend.topology_for_state(state) if style.get("topology_enabled", False) else None
        return (
            camera_payload,
            _camera_figure_patch(scene, style, camera, topology_data=topology_data),
            _fast_view_metadata(backend, state, camera_payload),
        )

    @app.callback(
        Output("view-projection", "value", allow_duplicate=True),
        Input("agent-state-store", "data"),
        prevent_initial_call=True,
    )
    def sync_view_projection_from_state(state):
        # Mirror ``state["projection"]`` onto the radio so externally
        # driven changes (REST mutations, scene switches) keep the UI
        # honest. The matched-value short-circuit in
        # ``apply_view_projection`` prevents the round-trip from
        # ratcheting the figure cache.
        if not isinstance(state, dict):
            return no_update
        return _coerce_projection(state.get("projection") or "perspective")

    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Output("crystal-graph", "figure", allow_duplicate=True),
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
        scene = backend.scene_for_state(state)
        style = backend.style_for_state(state, scene=scene)
        camera_payload = _camera_store_payload(scene_id, camera)
        topology_data = backend.topology_for_state(state) if style.get("topology_enabled", False) else None
        return (
            camera_payload,
            _camera_figure_patch(scene, style, camera, topology_data=topology_data),
            _fast_view_metadata(backend, state, camera_payload),
        )

    @app.callback(
        Output("camera-state-store", "data", allow_duplicate=True),
        Input("crystal-graph", "relayoutData"),
        State("camera-state-store", "data"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def capture_camera(relayout_data, camera_state, scene_id):
        scene_id = scene_id or backend.active_scene_id()
        if scene_id and scene_id not in backend.scene_store.scenes:
            return no_update
        relayout = relayout_data if isinstance(relayout_data, dict) else {}
        full_camera_payload = bool(
            isinstance(relayout.get("scene.camera"), dict)
            or (
                isinstance(relayout.get("scene"), dict)
                and isinstance((relayout.get("scene") or {}).get("camera"), dict)
            )
        )
        current_camera = _camera_from_store(camera_state, scene_id) or backend.get_state(scene_id).get("camera")
        camera = _camera_from_relayout_data(
            relayout,
            current_camera,
        )
        if not camera:
            return no_update
        if isinstance(current_camera, dict) and camera == current_camera:
            return no_update
        scene_key = str(scene_id or "")
        now = time.monotonic()
        last_commit = _last_camera_commit_by_scene.get(scene_key, 0.0)
        # Dragging emits dotted partial camera updates every frame; persisting
        # each one churns backend version and WS snapshots. Keep the server in
        # sync at ~4 Hz during motion, but always persist full-camera payloads
        # (mouseup/programmatic relayout) immediately.
        if not full_camera_payload and (now - last_commit) < _camera_commit_min_interval_s:
            return no_update
        # ``broadcast=False`` is essential here: the browser is the
        # source of truth for the camera, so we must NOT arm
        # ``pending_state`` -- otherwise the next 5 s ``agent-state-poll``
        # echoes this camera back through ``sync_agent_state`` ->
        # ``camera-state-store`` -> ``update_view`` and the figure
        # re-renders with whatever camera was captured at that exact
        # moment, snapping the user's view back periodically. See
        # ``tests/app/test_camera_capture_no_poll_echo.py``.
        backend.apply_intent(
            {"type": "set_camera", "scene_id": scene_id, "payload": {"camera": camera}}
        )
        _last_camera_commit_by_scene[scene_key] = now
        return _camera_store_payload(scene_id, camera)

    @app.callback(
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("agent-state-store", "data"),
        State("camera-state-store", "data"),
        prevent_initial_call=True,
    )
    def refresh_fast_view_metadata(agent_state, camera_state):
        state = backend.normalize_state(agent_state or backend.get_state())
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
        Input("graph-interaction-store", "data"),
        State("crystal-graph", "figure"),
        State("camera-state-store", "data"),
    )
    def update_view(
        agent_state,
        interaction_state,
        current_figure,
        camera_state,
    ):
        # ``update_view`` is the dominant cost when the user pokes a
        # slider or a colour swatch -- it rebuilds the figure, the
        # topology histogram, and the structure-summary table in one
        # callback. Wrap it so the perf log makes the total wall time
        # observable. ``figure_for_state`` itself is instrumented
        # internally with three sub-blocks (``scene_for_state``,
        # ``topology_for_state``, ``build_figure``) so the user can
        # tell which leg is slow without re-profiling.
        cb_start = time.monotonic()
        triggered = getattr(callback_context, "triggered_id", None)
        state = backend.normalize_state(agent_state or backend.get_state())
        scene_id = state.get("scene_id")
        interaction_active = bool((interaction_state or {}).get("active"))
        last_rendered_scene_id = getattr(update_view, "_last_rendered_scene_id", None)
        if triggered == "graph-interaction-store" and not interaction_active and last_rendered_scene_id == scene_id:
            # The browser emits ``graph-interaction-store.active=false``
            # when a drag/wheel gesture settles.  That edge only exists
            # to re-enable deferred updates; it is NOT a data change and
            # should not rebuild ``crystal-graph.figure``.  Otherwise the
            # ``dcc.Loading`` wrapper enters loading state right after
            # every zoom/rotate, which looks like the page gets covered
            # by a loading overlay even for a no-op camera-only gesture.
            perf_log.record(
                "callback:update_view",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={"scene_id": scene_id, "figure": "skip_interaction_settled"},
            )
            return no_update, no_update, no_update, no_update
        topo_key_preview = (
            state.get("scene_id"),
            state.get("structure"),
            state.get("display_mode"),
            tuple(state.get("topology_species_keys") or ()),
            state.get("topology_site_index"),
            state.get("topology_enabled"),
            state.get("cutoff"),
            "hydrogens" in (state.get("display_options") or []),
            transforms_cache_key(state.get("transforms") or []),
            tuple(
                (
                    s.get("id"),
                    s.get("center_species"),
                    s.get("ligand_species"),
                    s.get("color"),
                    bool(s.get("enabled", True)),
                    bool(s.get("enforce_enclosure", True)),
                    float(s.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)),
                )
                for s in (state.get("polyhedron_specs") or [])
            ),
            tuple(
                (
                    g.get("id"),
                    bool(g.get("visible", True)),
                    g.get("color"),
                    g.get("opacity"),
                    tuple(sorted((g.get("selector") or {}).get("elements") or [])) if (g.get("selector") or {}).get("elements") else None,
                    bool((g.get("selector") or {}).get("all", False)),
                    (g.get("selector") or {}).get("is_minor"),
                )
                for g in (state.get("atom_groups") or [])
            ),
            tuple(
                (
                    g.get("id"),
                    bool(g.get("visible", True)),
                    g.get("color"),
                    g.get("opacity"),
                    g.get("radius_scale"),
                    tuple(sorted((g.get("selector") or {}).get("between_elements") or []))
                    if (g.get("selector") or {}).get("between_elements")
                    else None,
                    bool((g.get("selector") or {}).get("all", False)),
                    (g.get("selector") or {}).get("is_minor"),
                )
                for g in (state.get("bond_groups") or [])
            ),
        )
        prev_key = getattr(update_view, "_topo_cache_key", None)
        topology_changed = prev_key != topo_key_preview
        if interaction_active and last_rendered_scene_id == scene_id and not topology_changed:
            perf_log.record(
                "callback:update_view",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={"scene_id": scene_id, "figure": "deferred_interaction"},
            )
            return no_update, no_update, no_update, no_update
        if interaction_active and last_rendered_scene_id == scene_id and topology_changed:
            patched = _polyhedron_visibility_patch_for_figure(current_figure, state)
            if patched is not no_update:
                perf_log.record(
                    "callback:update_view",
                    duration_ms=(time.monotonic() - cb_start) * 1000.0,
                    kind="cb",
                    info={
                        "scene_id": scene_id,
                        "figure": "deferred_interaction_polyhedra_patch",
                    },
                )
                return patched, no_update, no_update, no_update
        camera = _camera_from_store(camera_state, state.get("scene_id"))
        if camera:
            state["camera"] = camera
        # Topology overlay toggles are user-visible correctness changes.  Do
        # them synchronously so the checkbox never leaves a stale no-overlay
        # frame waiting on the background topology/websocket fast lane.
        fig, topology_data = backend.figure_for_state(state, async_topology=not topology_changed)
        if isinstance(fig, dict) and fig.get("_mattervis_pending"):
            perf_log.record(
                "callback:update_view",
                duration_ms=(time.monotonic() - cb_start) * 1000.0,
                kind="cb",
                info={
                    "scene_id": state.get("scene_id"),
                    "figure": "pending",
                },
            )
            return no_update, no_update, no_update, no_update
        # The right-hand sidebar only changes when the *topology* state
        # or the chosen scene changes. Keep a memo on the callback
        # itself so toggling Labels / Axes / Atom Scale -- which all
        # leave the topology untouched -- skips serialising the
        # histogram + markdown + structure summary every time. Each of
        # these is only ~1-3 kB but they re-render on the client, and
        # the markdown table tear-down was visible in the CPU profile.
        topo_key = topo_key_preview
        if prev_key == topo_key:
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
            return fig, no_update, no_update, no_update
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
        Output("status-dismiss-timer", "disabled"),
        Output("status-dismiss-timer", "n_intervals"),
        Input("save-preset-btn", "n_clicks"),
        Input("export-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def save_or_export(_, __):
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        if triggered == "export-btn":
            png = backend.render_current_png(backend.active_scene_id())
            scene_label = backend.get_state().get("scene_label") or "mattervis"
            filename = f"{scene_label.replace(os.sep, '_')}.png"
            message, class_name = _status_message(f"Export ready: {filename}", "success")
            return message, class_name, dcc.send_bytes(lambda buffer: buffer.write(png), filename), False, 0
        result = backend.save_preset()
        message, class_name = _status_message(f"Saved preset: {result['path']}", "success")
        return message, class_name, no_update, False, 0

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
