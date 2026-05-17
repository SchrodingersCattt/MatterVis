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


def register_state_callbacks(app, backend):
    def scene_control_outputs(state: dict[str, Any]) -> tuple[Any, ...]:
        scene_id = state.get("scene_id") or backend.active_scene_id()
        return (
            state.get("scene_label") or state["structure"],
            state["display_mode"],
            state["display_options"],
            state["atom_scale"],
            state["bond_radius"],
            state["minor_opacity"],
            state.get("material", "mesh"),
            state.get("style", "ball_stick"),
            state.get("disorder", "outline_rings"),
            state.get("ortep_mode", "ortep_axes"),
            state["axis_scale"],
            state["topology_site_index"],
            ["enabled"] if state.get("topology_enabled", False) else [],
            state,
            _camera_store_payload(scene_id, state.get("camera")),
        )

    @app.callback(
        Output("topology-site-index", "value", allow_duplicate=True),
        Input("crystal-graph", "clickData"),
        State("scene-tabs", "value"),
        State("display-mode-selector", "value"),
        State("display-options", "value"),
        prevent_initial_call=True,
    )
    def click_to_select_fragment(click_data, scene_id, display_mode, display_options):
        if not click_data or not click_data.get("points"):
            return no_update
        try:
            structure = backend.get_state(scene_id).get("structure")
            state = backend.normalize_state(
                {
                    "scene_id": scene_id,
                    "structure": structure,
                    "display_mode": display_mode,
                    "display_options": display_options,
                }
            )
            resolved = backend.resolve_topology_site(
                state=state,
                structure=structure,
                explicit_site=None,
                species_keys=None,
                click_data=click_data,
            )
        except Exception:
            return no_update
        return resolved if resolved is not None else no_update

    @app.callback(
        Output("topology-site-index", "options"),
        Output("topology-site-index", "value", allow_duplicate=True),
        Input("scene-tabs", "value"),
        Input("display-mode-selector", "value"),
        Input("display-options", "value"),
        State("topology-site-index", "value"),
        prevent_initial_call=True,
    )
    def refresh_fragment_options(scene_id, display_mode, display_options, current_value):
        # The fragment options reflect the *scene* fragments, so they
        # change when the user switches structures, display modes
        # (formula unit / unit cell / cluster), or toggles hydrogens.
        # When the previously analysed fragment falls outside the new
        # scene we clear the dropdown so the topology callback falls
        # back to the "first match of selected species" default.
        # Of the five Display checkboxes only Hydrogens affects which
        # fragments exist. The other four (Labels/Axes/Minor Only/
        # Unit Cell Box) all fire this callback too because they share
        # the ``display-options`` Input, but recomputing the options
        # would do nothing useful and ``backend.fragment_options`` can
        # easily hit ~1s on dense unit cells. Short-circuit those.
        hydrogens_on = "hydrogens" in (display_options or [])
        active_state = backend.get_state(scene_id)
        transforms_key = transforms_cache_key(active_state.get("transforms") or [])
        cache_key = (scene_id, display_mode, hydrogens_on, transforms_key)
        cached = getattr(refresh_fragment_options, "_cache", None)
        if cached is not None and cached[0] == cache_key:
            opts = cached[1]
        else:
            try:
                structure = active_state.get("structure")
                state = backend.normalize_state(
                    {
                        "scene_id": scene_id,
                        "structure": structure,
                        "display_mode": display_mode,
                        "display_options": display_options,
                    }
                )
            except Exception:
                return no_update, no_update
            opts = backend.fragment_options(state)
            refresh_fragment_options._cache = (cache_key, opts)
        valid_values = {opt["value"] for opt in opts}
        keep = current_value if current_value in valid_values else None
        # The ``topology-site-index.value`` Output also writes the
        # ``capture_state`` Input. Whenever we re-emit the same value
        # we still cause Dash to fire a second ``capture_state``; if
        # *that* returns ``no_update`` (which it will, since the patch
        # is identical), Dash 2.18 collapses the whole agent-state
        # update chain and ``update_view`` is never queued. Returning
        # ``no_update`` for ``value`` whenever it's already correct
        # avoids the spurious second capture entirely.
        prev_opts = getattr(refresh_fragment_options, "_last_opts", None)
        opts_out = no_update if prev_opts == opts else opts
        if opts_out is not no_update:
            refresh_fragment_options._last_opts = opts
        value_out = no_update if keep == current_value else keep
        return opts_out, value_out

    @app.callback(
        Output("scene-event-store", "data"),
        Output("status", "children"),
        Input("scene-new-tab-btn", "n_clicks"),
        Input("scene-rename-btn", "n_clicks"),
        Input("scene-tab-close-active", "n_clicks"),
        Input("scene-close-others-btn", "n_clicks"),
        Input({"type": "tab-close", "scene_id": ALL}, "n_clicks"),
        State("scene-tabs", "value"),
        State("scene-tab-rename-input", "value"),
        prevent_initial_call=True,
    )
    def dispatch_scene_tab_event(_, __, ___, ____, close_clicks, active_scene_id, label):
        triggered = getattr(callback_context, "triggered_id", None)
        if isinstance(triggered, dict):
            if not close_clicks or not any(close_clicks):
                return no_update, no_update
            action = "close-row"
        else:
            action = str(triggered or "")
        if not active_scene_id and action != "close-row":
            return no_update, no_update

        message = no_update
        try:
            if action == "scene-new-tab-btn":
                scene = backend.duplicate_scene(active_scene_id)
                message = f"Duplicated scene: {scene['label']}"
            elif action == "scene-rename-btn":
                scene = backend.update_scene(active_scene_id, {"label": label or ""})
                message = f"Renamed scene: {scene['label']}"
            elif action == "scene-tab-close-active":
                if len(backend.scene_options()) <= 1:
                    return no_update, "At least one scene tab must remain."
                backend.delete_scene(active_scene_id)
                message = "Closed scene."
            elif action == "scene-close-others-btn":
                if len(backend.scene_options()) <= 1:
                    return no_update, "Only one scene open — nothing to close."
                result = backend.delete_other_scenes(active_scene_id)
                n = len(result.get("removed") or [])
                message = f"Closed {n} other scene{'s' if n != 1 else ''}."
            elif action == "close-row":
                scene_id = triggered.get("scene_id") if isinstance(triggered, dict) else None
                if not scene_id:
                    return no_update, no_update
                if len(backend.scene_options()) <= 1:
                    return no_update, "At least one scene tab must remain."
                backend.delete_scene(scene_id)
                message = "Closed scene."
            else:
                return no_update, no_update
        except Exception as exc:
            return no_update, f"Scene action failed: {exc}"

        return {
            "seq": time.time(),
            "active_id": backend.active_scene_id(),
            "version": backend.version,
            "action": action,
        }, message

    @app.callback(
        Output("scene-tabs", "children"),
        Output("scene-tab-close-row", "children"),
        Output("scene-tabs", "value"),
        Input("scene-event-store", "data"),
        Input("native-upload-sync", "data"),
        Input("agent-state-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def manage_scene_tabs_dom(_scene_event, _native_upload_sync, _n_intervals):
        """Single writer for the scene tab DOM.

        Scene CRUD callbacks and native upload only mutate the backend store
        and emit events. This dispatcher rebuilds the visible tabs from
        ``backend.scene_options()`` so tab labels, close buttons, and active
        value cannot be written from competing callbacks.
        """
        options = backend.scene_options()
        if not options:
            return no_update, no_update, no_update
        active_id = backend.active_scene_id() or options[0]["id"]
        return backend.scene_tabs(), backend.scene_close_buttons(), active_id

    @app.callback(
        Output("status-banner", "children", allow_duplicate=True),
        Output("status-banner", "className", allow_duplicate=True),
        Output("status-dismiss-timer", "disabled", allow_duplicate=True),
        Output("status-dismiss-timer", "n_intervals", allow_duplicate=True),
        Input("status", "children"),
        prevent_initial_call=True,
    )
    def mirror_legacy_status(message):
        if not message:
            return no_update, no_update, no_update, no_update
        text = str(message)
        level = "success"
        lowered = text.lower()
        if "failed" in lowered or "error" in lowered:
            level = "error"
        elif "must" in lowered or "warning" in lowered:
            level = "warning"
        return text, _status_class(level), False, 0

    # IMPORTANT: tab-switching (scene-tabs.value) and the agent-state
    # poll (agent-state-poll.n_intervals) still share this callback for
    # the *control props*. The scene tab DOM itself is handled by
    # ``manage_scene_tabs_dom`` above, which is the only writer for
    # ``scene-tabs.children`` / ``scene-tabs.value``.
    @app.callback(
        Output("scene-tab-rename-input", "value"),
        Output("display-mode-selector", "value"),
        Output("display-options", "value"),
        Output("atom-scale-slider", "value"),
        Output("bond-radius-slider", "value"),
        Output("minor-opacity-slider", "value"),
        Output("material-selector", "value"),
        Output("style-selector", "value"),
        Output("disorder-selector", "value"),
        Output("ortep-mode-selector", "value"),
        Output("axis-scale-slider", "value"),
        Output("topology-site-index", "value"),
        Output("topology-toggle", "value"),
        Output("agent-state-store", "data"),
        Output("camera-state-store", "data"),
        Input("agent-state-poll", "n_intervals"),
        Input("native-upload-sync", "data"),
        Input("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def sync_agent_state(_n_intervals, _native_upload_sync, scene_id):
        triggered = (
            callback_context.triggered[0]["prop_id"].split(".")[0]
            if callback_context.triggered
            else None
        )
        n_outputs = 15
        if triggered == "scene-tabs":
            if not scene_id:
                return (no_update,) * n_outputs
            backend.set_active_scene(scene_id, broadcast=False)
            state = backend.get_state(scene_id)
            return scene_control_outputs(state)
        state = backend.pop_pending_state()
        if not state:
            return (no_update,) * n_outputs
        # Defence-in-depth against the camera-snap-back bug: even when
        # the poll path legitimately picks up an externally-driven
        # state change (REST agent, WebSocket, scene CRUD), do NOT push
        # the stored camera back into ``camera-state-store``. The
        # browser already owns the camera; overwriting it with whatever
        # was last captured (potentially several seconds stale because
        # of Plotly's relayout debouncing) yanks the user's view
        # mid-rotation. ``capture_camera`` is the single writer for
        # camera-state-store on the UI path; the REST surface should
        # use the dedicated ``/api/v2/camera`` endpoint to push a
        # camera change to the browser.
        outputs = list(scene_control_outputs(state))
        outputs[-1] = no_update  # camera-state-store slot
        return tuple(outputs)

    @app.callback(
        Output("agent-state-store", "data", allow_duplicate=True),
        Input("scene-tabs", "value"),
        Input("display-mode-selector", "value"),
        Input("display-options", "value"),
        Input("atom-scale-slider", "value"),
        Input("bond-radius-slider", "value"),
        Input("minor-opacity-slider", "value"),
        Input("material-selector", "value"),
        Input("style-selector", "value"),
        Input("disorder-selector", "value"),
        Input("ortep-mode-selector", "value"),
        Input("axis-scale-slider", "value"),
        Input("topology-site-index", "value"),
        Input("topology-toggle", "value"),
        prevent_initial_call=True,
    )
    def capture_state(
        scene_id,
        display_mode,
        display_options,
        atom_scale,
        bond_radius,
        minor_opacity,
        material,
        render_style,
        disorder,
        ortep_mode,
        axis_scale,
        site_index,
        topology_toggle,
    ):
        triggered = callback_context.triggered[0]["prop_id"].split(".")[0] if callback_context.triggered else None
        if triggered == "scene-tabs":
            return no_update
        if scene_id:
            backend.set_active_scene(scene_id, broadcast=False)
        prev = backend.get_state(scene_id)
        prev_options = set(prev.get("display_options") or [])
        next_options = set(display_options or [])
        hydrogens_changed = ("hydrogens" in prev_options) != ("hydrogens" in next_options)
        display_changed = display_mode != prev.get("display_mode")
        patch: dict[str, Any] = {
            "scene_id": scene_id,
            "display_mode": display_mode,
            "display_options": display_options,
            "atom_scale": atom_scale,
            "bond_radius": bond_radius,
            "minor_opacity": minor_opacity,
            "material": material or "mesh",
            "style": render_style or "ball_stick",
            "disorder": disorder or "outline_rings",
            "ortep_mode": ortep_mode or "ortep_axes",
            "axis_scale": axis_scale,
            "topology_site_index": None if display_changed or site_index in ("", None) else int(site_index),
            "topology_enabled": "enabled" in (topology_toggle or []),
            "fast_rendering": material == "flat",
        }
        fast_display_options = (
            triggered != "display-options"
            or _display_options_can_fast_patch(prev_options, next_options)
        )
        if (
            triggered in {"display-options", "axis-scale-slider", "minor-opacity-slider"}
            and not hydrogens_changed
            and fast_display_options
        ):
            # Style-only controls are patched directly onto the current
            # Plotly figure by ``patch_fast_style_controls`` below. Persist
            # their state for API callers, but do not touch
            # ``agent-state-store`` or the full-figure callback.
            if all(prev.get(k) == v for k, v in patch.items() if k != "scene_id"):
                return no_update
            backend.record_state(patch)
            perf_log.record(
                "callback:capture_state",
                kind="cb",
                info={
                    "trigger": triggered,
                    "scene_id": scene_id,
                    "fast_path": True,
                },
            )
            return no_update
        # Skip the write -- and the cascade through ``update_view`` --
        # if every captured field already matches the persisted state.
        # The chain ``Labels click -> capture_state -> agent-state-store
        # -> refresh_fragment_options -> topology-site-index.value ->
        # capture_state -> agent-state-store`` would otherwise double up
        # every figure render, doubling the 1.4 MB-per-frame cost.
        if all(prev.get(k) == v for k, v in patch.items() if k != "scene_id"):
            return no_update
        backend.record_state(patch)
        perf_log.record(
            "callback:capture_state",
            kind="cb",
            info={
                "trigger": triggered,
                "scene_id": scene_id,
            },
        )
        return backend.get_state()

    @app.callback(
        Output("crystal-graph", "figure", allow_duplicate=True),
        Output("fast-view-metadata", "children", allow_duplicate=True),
        Input("display-options", "value"),
        Input("axis-scale-slider", "value"),
        Input("minor-opacity-slider", "value"),
        State("crystal-graph", "figure"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def patch_fast_style_controls(display_options, axis_scale, minor_opacity, current_figure, scene_id):
        """Patch style-only trace attributes without rebuilding the figure.

        Hydrogens remain on the full scene path because they change the atom
        and bond sets. Labels/axes/unit-cell/minor-only/minor-opacity only
        flip trace visibility/opacity, so a small Dash Patch is enough.
        """
        scene_id = scene_id or backend.active_scene_id()
        prev = backend.get_state(scene_id)
        prev_options = set(prev.get("display_options") or [])
        next_options = set(display_options or [])
        if ("hydrogens" in prev_options) != ("hydrogens" in next_options):
            return no_update, no_update
        if not _display_options_can_fast_patch(prev_options, next_options):
            return no_update, no_update
        patch_payload = {
            "display_options": list(display_options or []),
            "axis_scale": axis_scale,
            "minor_opacity": minor_opacity,
        }
        backend.record_state(patch_payload, scene_id=scene_id)
        fig_patch = _fast_style_patch_for_figure(
            current_figure,
            display_options=display_options,
            minor_opacity=minor_opacity,
        )
        return fig_patch, _fast_view_metadata(backend, backend.get_state(scene_id))

    # ------------------------------------------------------------------
    # Phase 3 UI: Named-polyhedra table.
    #
    # ONE callback handles Add / Delete (pattern-matched) / inline edit
    # (pattern-matched ALL inputs) / scene-change. Dispatch is by
    # ``callback_context.triggered_id``:
    #
    # - "polyhedra-add-btn" / "scene-tabs" -> rebuild children from
    #   backend state (the inline ALL inputs are stale during these
    #   triggers because the row count just changed).
    # - dict with "type": "poly-row-delete" -> remove the row whose
    #   spec_id is in the triggered_id, rebuild children.
    # - dict with "type": "poly-row-color" / "...-center" / "...-ligand"
    #   / "...-enabled" -> reconstruct the spec list from the live ALL
    #   inputs, persist via ``patch_state``, return ``no_update`` so we
    #   don't tear down the row React keys mid-edit.
    # ------------------------------------------------------------------
