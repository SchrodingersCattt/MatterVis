from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from ..api import register_api
from .camera_helpers import *
from .editor_tables import *
from .editor_transforms import *
from .editor_operations import *
from .runtime import _install_callback_audit, _start_cache_prewarm
from .style_helpers import *
from .callbacks_editors import register_editor_callbacks
from .callbacks_analysis import register_analysis_callbacks
from .callbacks_operations import register_operations_callbacks
from .callbacks_disorder import register_disorder_callbacks
from .callbacks_state import register_state_callbacks
from .callbacks_view import register_view_callbacks
from .backend import ViewerBackend


def create_app(
    preset_path: str = DEFAULT_PRESET_PATH,
    names=None,
    root_dir: Optional[str] = None,
    cif_paths: Optional[Iterable[str]] = None,
) -> Dash:
    backend = ViewerBackend(preset_path=preset_path, names=names, root_dir=root_dir)
    for cif_path in cif_paths or []:
        bundle = build_loaded_crystal(
            name=os.path.splitext(os.path.basename(cif_path))[0],
            cif_path=cif_path,
            title=os.path.splitext(os.path.basename(cif_path))[0],
            preset=backend.preset,
            source="cli",
        )
        backend.bundles[bundle.name] = bundle
        if bundle.name not in backend.structure_names:
            backend.structure_names.append(bundle.name)
        if not any(scene["structure_name"] == bundle.name for scene in backend.scene_options()):
            backend.create_scene(structure=bundle.name, label=bundle.name)
    if cif_paths:
        backend._drop_placeholder()
    if backend.structure_names and backend.current_state.get("structure") not in backend.structure_names:
        backend.current_state = backend.default_state(backend.structure_names[0])
    if backend.scene_store.active_id:
        backend.current_state = backend.scene_state(backend.scene_store.active_id)
    app = Dash(
        __name__,
        assets_folder=os.path.join(WORKSPACE_DIR, "frontend", "assets"),
    )
    app.crystal_backend = backend

    # gzip + brotli the JSON figure responses. ``update_view`` ships
    # ~1 MB of base64 mesh data per click and most of that string
    # alphabet is plain ASCII, so it compresses to ~150-250 kB. On
    # any user with <2 Mbit/s downstream that's the difference
    # between a Labels-toggle taking ~5 s and ~0.5 s. Flask-Compress
    # only kicks in for ``Accept-Encoding`` clients and skips bodies
    # below ``COMPRESS_MIN_SIZE``, so it has no effect on the tiny
    # capture_state / poll responses.
    try:
        from flask_compress import Compress

        app.server.config.setdefault("COMPRESS_MIMETYPES", [
            "text/html", "text/css", "text/javascript",
            "application/javascript", "application/json", "application/octet-stream",
        ])
        app.server.config.setdefault("COMPRESS_LEVEL", 6)
        app.server.config.setdefault("COMPRESS_BR_LEVEL", 4)
        app.server.config.setdefault("COMPRESS_MIN_SIZE", 1024)
        Compress(app.server)
    except Exception:
        # Compression is opportunistic; the app must still serve
        # without it (e.g. on a stripped-down install).
        pass

    _warm_state = backend.get_state()
    backend.figure_for_state(_warm_state)
    backend._first_figure_ready.set()

    def _serve_layout():
        # Dash invokes a callable layout on every initial-load
        # request, so a browser refresh after the user uploaded a
        # CIF (or switched tabs) no longer hands back the static
        # startup snapshot. Each call re-derives the scene tabs
        # value, slider defaults and dcc.Store payloads from the
        # live backend so the page comes back exactly where the
        # user left it. The agent-state-poll Interval below is
        # still the steady-state sync path; this just removes the
        # 5 s "default screen" window between page reload and the
        # first poll tick.
        try:
            return _serve_layout_inner()
        except Exception:
            import traceback
            tb = traceback.format_exc()
            try:
                from .. import perf_log
                perf_log.record("_serve_layout:CRASH", kind="error", info={"traceback": tb[:2000]})
            except Exception:
                pass
            # Return a minimal fallback layout so the user can at least
            # see the error and switch scenes / upload a new CIF.
            first_state = backend.get_state()
            return html.Div([
                dcc.Store(id="agent-state-store", data=first_state),
                dcc.Store(id="camera-state-store", data=_camera_store_payload(first_state.get("scene_id"), first_state.get("camera"))),
                dcc.Store(id="fast-ui-event-store", data=None),
                html.Div(id="fast-view-metadata", children="", style={"display": "none"}),
                dcc.Store(id="native-upload-sync", data={"seq": 0}),
                dcc.Store(id="scene-event-store", data={"seq": 0}),
                dcc.Store(id="graph-interaction-store", data={"active": False, "ts": 0}),
                dcc.Store(id="disorder-replicas-store", data={"replicas": [], "scene_id": None, "status": "idle"}),
                dcc.Store(id="disorder-hover-id", data=None),
                dcc.Store(id="disorder-preview-sink", data=None),
                dcc.Store(id="disorder-persist-sink", data=None),
                dcc.Download(id="export-download"),
                dcc.Interval(id="status-dismiss-timer", interval=5000, n_intervals=0, disabled=True),
                dcc.Interval(id="agent-state-poll", interval=30000, n_intervals=0),
                html.Div(id="state-sync-sentinel", style={"display": "none"}),
                dcc.Store(id="rightclick-target", data=None),
                dcc.Input(id="rightclick-target-fallback", type="hidden", value="", debounce=False),
                html.Div(id="rightclick-menu", className="rightclick-menu rightclick-menu--hidden", children=[], style={"top": "0px", "left": "0px"}),
                html.Div(id="kbd-help", className="kbd-help kbd-help--hidden", children=[]),
                html.Div(
                    f"⚠️ Layout generation failed. Check Server log for details.\n\n{tb[:500]}",
                    style={"whiteSpace": "pre-wrap", "padding": "2em", "color": "#c00", "fontFamily": "monospace"},
                ),
            ])

    def _serve_layout_inner():
        first_state = backend.get_state()
        disorder_resolve = first_state.get("disorder_resolve") or {}
        first_figure, first_topology = backend.figure_for_state(first_state)
        first_scene = backend.scene_for_state(first_state)
        return html.Div(
            [
                dcc.Store(id="agent-state-store", data=first_state),
                dcc.Store(
                    id="camera-state-store",
                    data=_camera_store_payload(first_state.get("scene_id"), first_state.get("camera")),
                ),
                dcc.Store(id="fast-ui-event-store", data=None),
                html.Div(
                    id="fast-view-metadata",
                    children=_fast_view_metadata(
                        backend,
                        first_state,
                        _camera_store_payload(first_state.get("scene_id"), first_state.get("camera")),
                    ),
                    style={"display": "none"},
                ),
                dcc.Store(id="native-upload-sync", data={"seq": 0}),
                dcc.Store(id="scene-event-store", data={"seq": 0}),
                dcc.Store(id="graph-interaction-store", data={"active": False, "ts": 0}),
                dcc.Store(id="disorder-replicas-store", data={"replicas": [], "scene_id": None, "status": "idle"}),
                dcc.Store(id="disorder-hover-id", data=None),
                dcc.Store(id="disorder-preview-sink", data=None),
                dcc.Store(id="disorder-persist-sink", data=None),
                dcc.Download(id="export-download"),
                dcc.Interval(id="status-dismiss-timer", interval=5000, n_intervals=0, disabled=True),
                # 30 s fallback poll — the WS fast lane in mattervis.js
                # pushes state changes immediately, so this interval only
                # serves as a safety net for missed pushes or reconnects.
                dcc.Interval(id="agent-state-poll", interval=30000, n_intervals=0),
                html.Div(id="state-sync-sentinel", style={"display": "none"}),
                # Phase 4: right-click + keyboard shortcut wiring -----------
                # The JS in ``assets/right_click_menu.js`` writes the
                # picked-target payload into ``rightclick-target.data``;
                # ``assets/keyboard_shortcuts.js`` writes the same store but
                # with an extra ``action`` field for one-key dispatch.
                # ``rightclick-target-fallback`` is a defensive hidden input
                # the JS uses if ``dash_clientside.set_props`` is not yet
                # bootstrapped (e.g. very early page load); a tiny callback
                # keeps the store in sync with that input.
                dcc.Store(id="rightclick-target", data=None),
                dcc.Input(
                    id="rightclick-target-fallback",
                    type="hidden",
                    value="",
                    debounce=False,
                ),
                html.Div(
                    id="rightclick-menu",
                    className="rightclick-menu rightclick-menu--hidden",
                    children=[],
                    style={"top": "0px", "left": "0px"},
                ),
                html.Div(
                    id="kbd-help",
                    className="kbd-help kbd-help--hidden",
                    children=[
                        html.Button(
                            "\u00d7",
                            id="kbd-help-close",
                            n_clicks=0,
                            className="kbd-help__close",
                            title="Close",
                        ),
                        html.Div("Keyboard shortcuts", className="kbd-help__title"),
                        html.Div(
                            [
                                html.Span("?", className="kbd-help__key"),
                                html.Span("Toggle this panel"),
                            ],
                            className="kbd-help__row",
                        ),
                        html.Div(
                            [
                                html.Span("r", className="kbd-help__key"),
                                html.Span("Repeat 2\u00d72\u00d72 (replace existing)"),
                            ],
                            className="kbd-help__row",
                        ),
                        html.Div(
                            [
                                html.Span("Shift+r", className="kbd-help__key"),
                                html.Span("Clear repeat (back to home cell)"),
                            ],
                            className="kbd-help__row",
                        ),
                        html.Div(
                            [
                                html.Span("g", className="kbd-help__key"),
                                html.Span("Grow by 1 bond hop from hovered atom"),
                            ],
                            className="kbd-help__row",
                        ),
                        html.Div(
                            [
                                html.Span("Shift+g", className="kbd-help__key"),
                                html.Span("Grow by 4\u202f\u00c5 from hovered atom"),
                            ],
                            className="kbd-help__row",
                        ),
                        html.Div(
                            [
                                html.Span("h", className="kbd-help__key"),
                                html.Span("Hide hovered atom / bond / polyhedron"),
                            ],
                            className="kbd-help__row",
                        ),
                        html.Div(
                            [
                                html.Span("c", className="kbd-help__key"),
                                html.Span("Open colour picker for hovered target"),
                            ],
                            className="kbd-help__row",
                        ),
                        html.Div(
                            [
                                html.Span("p", className="kbd-help__key"),
                                html.Span("Promote hovered atom to a group rule"),
                            ],
                            className="kbd-help__row",
                        ),
                    ],
                ),
                html.Div(
                    [
                        html.H3("Crystal Viewer", style={"marginTop": "0"}),
                        html.Div(
                            [
                                html.Label("Scenes", style={"fontWeight": "bold"}),
                                html.Div(
                                    [
                                        html.Button(
                                            "Close others",
                                            id="scene-close-others-btn",
                                            n_clicks=0,
                                            title="Close every scene except the active one",
                                            className="scene-batch-close-btn",
                                            style={"marginRight": "6px"},
                                        ),
                                        html.Button(
                                            "+",
                                            id="scene-new-tab-btn",
                                            n_clicks=0,
                                            title="Duplicate active scene as new tab",
                                        ),
                                        html.Span("Duplicate tab", className="scene-new-tab-hint"),
                                    ],
                                    style={"float": "right"},
                                ),
                            ],
                            style={"marginBottom": "4px"},
                        ),
                        dcc.Tabs(
                            id="scene-tabs",
                            value=first_state.get("scene_id") or backend.active_scene_id(),
                            children=backend.scene_tabs(),
                            parent_className="scene-tabs",
                        ),
                        html.Div(
                            id="scene-tab-close-row",
                            children=backend.scene_close_buttons(),
                            className="scene-tab-close-row",
                        ),
                        html.Div(
                            [
                                dcc.Input(
                                    id="scene-tab-rename-input",
                                    type="text",
                                    value=first_state.get("scene_label") or first_state["structure"],
                                    placeholder="Scene label",
                                    style={"width": "68%", "marginRight": "6px"},
                                ),
                                html.Button("Rename", id="scene-rename-btn", n_clicks=0),
                                html.Button("Close", id="scene-tab-close-active", n_clicks=0, style={"marginLeft": "6px"}),
                            ],
                            style={"marginTop": "8px", "marginBottom": "8px"},
                        ),
                        html.Div(
                            id="structure-summary",
                            children=_structure_summary(first_scene),
                            style={"marginBottom": "12px", "fontSize": "13px", "color": "#444444"},
                        ),
                        html.Label("Upload CIF"),
                        html.Div(
                            [
                                dcc.Input(
                                    id="scene-cif-upload-input",
                                    type="file",
                                    multiple=True,
                                    style={"display": "none"},
                                ),
                                html.Div(
                                    "Drag and drop CIF, or click to upload",
                                    id="scene-cif-upload",
                                    role="button",
                                    tabIndex=0,
                                    **{"aria-label": "Upload CIF"},
                                    style={
                                        "border": "1px dashed #999999",
                                        "padding": "10px",
                                        "marginBottom": "12px",
                                        "textAlign": "center",
                                        "cursor": "pointer",
                                        "userSelect": "none",
                                    },
                                ),
                            ],
                        ),
                        html.Div(
                            id="upload-status",
                            style={"marginBottom": "12px", "whiteSpace": "pre-wrap", "fontSize": "13px"},
                        ),
                        html.Label("Display Scope"),
                        dcc.Dropdown(
                            id="display-mode-selector",
                            options=[
                                {"label": "Formula unit cluster", "value": "formula_unit"},
                                {"label": "Unit cell", "value": "unit_cell"},
                                {"label": "Asymmetric unit", "value": "asymmetric_unit"},
                                {"label": "Isolated cluster (no PBC)", "value": "cluster"},
                            ],
                            value=first_state["display_mode"],
                            clearable=False,
                            style={"marginBottom": "12px"},
                        ),
                        html.Label("Display"),
                        dcc.Checklist(
                            id="display-options",
                            options=[
                                {"label": "Labels", "value": "labels"},
                                {"label": "Axes", "value": "axes"},
                                {"label": "Minor Only", "value": "minor_only"},
                                {"label": "Hydrogens", "value": "hydrogens"},
                                {"label": "Unit Cell Box", "value": "unit_cell_box"},
                                # Phase 3: legacy "Monochrome atoms" toggle
                                # has been replaced by the Atom-Groups
                                # editor below (one-click "Monochrome"
                                # preset). Backend still honours the
                                # ``monochrome`` flag for callers / saved
                                # presets that set it directly.
                            ],
                            value=[opt for opt in first_state["display_options"] if opt != "monochrome"],
                        ),
                        html.Div(style={"height": "10px"}),
                        # ---- Phase 4 (view tools): VESTA-style axis-aligned
                        # views + perspective / orthographic toggle.
                        #
                        # Six small buttons map to ``align`` actions on the
                        # backend; the radio mirrors ``state["projection"]``.
                        # All wiring lives in ``apply_view_action`` /
                        # ``apply_view_projection`` callbacks below.
                        html.Label("View"),
                        html.Div(
                            [
                                html.Button(
                                    "a", id="view-align-a", n_clicks=0,
                                    className="view-align-btn",
                                    title="Look down lattice axis a",
                                ),
                                html.Button(
                                    "b", id="view-align-b", n_clicks=0,
                                    className="view-align-btn",
                                    title="Look down lattice axis b",
                                ),
                                html.Button(
                                    "c", id="view-align-c", n_clicks=0,
                                    className="view-align-btn",
                                    title="Look down lattice axis c",
                                ),
                                html.Button(
                                    "a*", id="view-align-astar", n_clicks=0,
                                    className="view-align-btn",
                                    title="Look down reciprocal axis a*",
                                ),
                                html.Button(
                                    "b*", id="view-align-bstar", n_clicks=0,
                                    className="view-align-btn",
                                    title="Look down reciprocal axis b*",
                                ),
                                html.Button(
                                    "c*", id="view-align-cstar", n_clicks=0,
                                    className="view-align-btn",
                                    title="Look down reciprocal axis c*",
                                ),
                                html.Button(
                                    "Reset", id="view-reset", n_clicks=0,
                                    className="view-align-btn view-reset-btn",
                                    title="Reset to scene-default camera",
                                ),
                            ],
                            className="view-align-row",
                        ),
                        dcc.RadioItems(
                            id="view-projection",
                            options=[
                                {"label": "Perspective", "value": "perspective"},
                                {"label": "Orthographic", "value": "orthographic"},
                            ],
                            value=str(first_state.get("projection", "perspective")),
                            inline=True,
                            className="view-projection-row",
                        ),
                        html.Div(style={"height": "10px"}),
                        html.Label("Material / Style / Disorder"),
                        html.Div(
                            [
                                dcc.Dropdown(
                                    id="material-selector",
                                    options=[
                                        {"label": "3D Mesh", "value": "mesh"},
                                        {"label": "2D Flat", "value": "flat"},
                                    ],
                                    value=first_state.get("material", "mesh"),
                                    clearable=False,
                                    style={"flex": "1"},
                                ),
                                dcc.Dropdown(
                                    id="style-selector",
                                    options=[
                                        {"label": "Ball-stick", "value": "ball_stick"},
                                        {"label": "Ball", "value": "ball"},
                                        {"label": "Stick", "value": "stick"},
                                        {"label": "ORTEP", "value": "ortep"},
                                        {"label": "Wireframe", "value": "wireframe"},
                                    ],
                                    value=first_state.get("style", "ball_stick"),
                                    clearable=False,
                                    style={"flex": "1"},
                                ),
                                dcc.Dropdown(
                                    id="disorder-selector",
                                    options=[
                                        {"label": "Outline rings", "value": "outline_rings"},
                                        {"label": "Opacity from occ.", "value": "opacity"},
                                        {"label": "Dashed bonds", "value": "dashed_bonds"},
                                        {"label": "Colour shift", "value": "color_shift"},
                                        {"label": "None", "value": "none"},
                                    ],
                                    value=first_state.get("disorder", "outline_rings"),
                                    clearable=False,
                                    style={"flex": "1"},
                                ),
                            ],
                            style={"display": "flex", "gap": "6px", "marginBottom": "10px"},
                        ),
                        html.Label("ORTEP Draw Mode"),
                        dcc.Dropdown(
                            id="ortep-mode-selector",
                            options=[
                                {"label": "Solid ellipsoids", "value": "ortep_solid"},
                                {"label": "Principal axes", "value": "ortep_axes"},
                                {"label": "Octant shading", "value": "ortep_octant"},
                                {"label": "Publication hatch", "value": "ortep_hatch"},
                            ],
                            value=first_state.get("ortep_mode", "ortep_axes"),
                            clearable=False,
                            style={"marginBottom": "10px"},
                        ),
                        html.Label("Atom Scale"),
                        dcc.Slider(
                            id="atom-scale-slider",
                            min=0.5, max=1.8, step=0.02,
                            value=float(first_state["atom_scale"]),
                            marks={0.5: "0.5", 1.0: "1.0", 1.5: "1.5", 1.8: "1.8"},
                            tooltip={"placement": "bottom", "always_visible": False},
                            updatemode="mouseup",
                        ),
                        html.Label("Bond Radius"),
                        dcc.Slider(
                            id="bond-radius-slider",
                            min=0.05, max=0.40, step=0.01,
                            value=float(first_state["bond_radius"]),
                            marks={0.05: "0.05", 0.20: "0.20", 0.40: "0.40"},
                            tooltip={"placement": "bottom", "always_visible": False},
                            updatemode="mouseup",
                        ),
                        html.Div(
                            [
                                html.Label("Minor Opacity"),
                                dcc.Slider(
                                    id="minor-opacity-slider",
                                    min=0.10, max=0.90, step=0.02,
                                    value=float(first_state["minor_opacity"]),
                                    marks={0.1: "0.1", 0.5: "0.5", 0.9: "0.9"},
                                    tooltip={"placement": "bottom", "always_visible": False},
                                    updatemode="mouseup",
                                    disabled=_minor_opacity_disabled(first_state.get("disorder", "outline_rings")),
                                ),
                            ],
                            id="minor-opacity-control",
                            style=_minor_opacity_control_style(first_state.get("disorder", "outline_rings")),
                        ),
                        html.Label("Axis Scale"),
                        dcc.Slider(
                            id="axis-scale-slider",
                            min=0.05, max=0.25, step=0.01,
                            value=float(first_state["axis_scale"]),
                            marks={0.05: "0.05", 0.15: "0.15", 0.25: "0.25"},
                            tooltip={"placement": "bottom", "always_visible": False},
                            updatemode="mouseup",
                        ),
                        html.Hr(),
                        # ---- Phase 3: Atom groups table ----
                        html.Div(
                            [
                                html.H4(
                                    "Atom groups",
                                    style={"display": "inline-block", "marginRight": "8px"},
                                ),
                                html.Button(
                                    "+ Add",
                                    id="atom-groups-add-btn",
                                    n_clicks=0,
                                    style={
                                        "fontSize": "12px",
                                        "padding": "2px 8px",
                                        "verticalAlign": "middle",
                                        "cursor": "pointer",
                                    },
                                    title="Add an empty atom-group rule. Pick a selector (all / by-element) and a colour.",
                                ),
                            ],
                            style={"display": "flex", "alignItems": "center"},
                        ),
                        html.Div(
                            [
                                html.Button(
                                    "Monochrome",
                                    id="atom-groups-preset-mono",
                                    n_clicks=0,
                                    style={"fontSize": "12px", "padding": "2px 8px", "marginRight": "4px", "cursor": "pointer"},
                                    title="Add an 'all atoms = #000000' rule (replacement for the legacy Monochrome checkbox).",
                                ),
                                html.Button(
                                    "Clear all",
                                    id="atom-groups-clear-btn",
                                    n_clicks=0,
                                    style={"fontSize": "12px", "padding": "2px 8px", "cursor": "pointer", "color": "#A00"},
                                    title="Drop every atom-group rule for this scene.",
                                ),
                            ],
                            style={"marginTop": "6px"},
                        ),
                        html.Div(
                            "Tip: to hide hydrogens use the Hydrogens checkbox under Display "
                            "Options above; that path also rebuilds bonds correctly. "
                            "Atom-group rules tweak per-atom colour / opacity / material.",
                            style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                        ),
                        html.Div(
                            id="atom-groups-rows-container",
                            children=_atom_groups_table_rows(
                                first_state.get("atom_groups") or [],
                                backend.element_options(first_state),
                            ),
                            style={"marginTop": "6px"},
                        ),
                        html.Hr(),
                        # ---- Phase 4: Bond groups table ----
                        html.Div(
                            [
                                html.H4(
                                    "Bond groups",
                                    style={"display": "inline-block", "marginRight": "8px"},
                                ),
                                html.Button(
                                    "+ Add",
                                    id="bond-groups-add-btn",
                                    n_clicks=0,
                                    style={
                                        "fontSize": "12px",
                                        "padding": "2px 8px",
                                        "verticalAlign": "middle",
                                        "cursor": "pointer",
                                    },
                                    title="Add a bond-styling rule (selector + colour / opacity / radius scale).",
                                ),
                            ],
                            style={"display": "flex", "alignItems": "center"},
                        ),
                        html.Div(
                            "Per-rule overrides for bond colour, visibility, opacity, and "
                            "radius. Selector \u2018between elements\u2019 picks a Pb\u2013Cl style; "
                            "\u2018minor only\u2019 / \u2018major only\u2019 follow disorder flags.",
                            style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                        ),
                        html.Div(
                            id="bond-groups-rows-container",
                            children=_bond_groups_table_rows(
                                first_state.get("bond_groups") or [],
                                backend.element_options(first_state),
                            ),
                            style={"marginTop": "6px"},
                        ),
                        html.Hr(),
                        html.Div(style={"height": "12px"}),
                        html.Button("Save Preset", id="save-preset-btn", n_clicks=0),
                        html.Button("Export Static Figure", id="export-btn", n_clicks=0, style={"marginLeft": "8px"}),
                        html.Div(
                            id="status-banner",
                            children=f"Preset: {preset_path}",
                            className=_status_class("idle"),
                        ),
                        html.Div(id="status", style={"display": "none"}),
                    ],
                    id="left-panel",
                    style={
                        "width": "340px",
                        "minWidth": "260px",
                        "maxWidth": "640px",
                        "flex": "0 0 auto",
                        "padding": "16px",
                        "borderRight": "1px solid #DDDDDD",
                        "fontFamily": "Arial, sans-serif",
                        "overflowY": "auto",
                        "height": "100vh",
                    },
                ),
                html.Div(id="left-splitter", className="panel-splitter"),
                html.Div(
                    [
                        dcc.Loading(
                            dcc.Graph(
                                id="crystal-graph",
                                figure=first_figure,
                                style={"height": "100%", "width": "100%"},
                                config={"responsive": True},
                            ),
                            type="circle",
                            color="#7C5CBF",
                            # Avoid a spinner flash on every short callback
                            # (capture_state is ~10 ms; a spinner that
                            # appears for 50 ms reads as a stutter, not
                            # progress). The 300 ms threshold is short
                            # enough that on slow updates (cold figure
                            # rebuild ~1.5 s, dense topology ~600 ms)
                            # the user still gets feedback well before
                            # they would start wondering if the click
                            # registered.
                            delay_show=300,
                            delay_hide=0,
                            style={"height": "100%", "width": "100%"},
                            parent_style={"height": "100%", "width": "100%"},
                        )
                    ],
                    id="center-panel",
                    style={
                        "flex": "1 1 auto",
                        "minWidth": 0,
                        "minHeight": 0,
                        "height": "100vh",
                        "overflow": "hidden",
                    },
                ),
                html.Div(id="right-splitter", className="panel-splitter"),
                html.Div(
                    [
                        html.Div(
                            [
                                html.Button(
                                    "Analysis",
                                    id="analysis-panel-toggle",
                                    className="analysis-panel-toggle",
                                    n_clicks=0,
                                    title="Show or hide analysis panel",
                                ),
                                html.Button(
                                    "Operation",
                                    id="operation-panel-toggle",
                                    className="analysis-panel-toggle operation-panel-toggle",
                                    n_clicks=0,
                                    title="Show operation panel",
                                ),
                                html.Div(
                                    [
                                        html.Div("Analysis", className="analysis-panel-title"),
                                        html.Div(
                                            "Topology, score summaries, and future analysis modules.",
                                            className="analysis-panel-subtitle",
                                        ),
                                    ],
                                    className="analysis-panel-heading",
                                ),
                            ],
                            className="analysis-panel-header",
                        ),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.Section(
                                            [
                                                html.Div("Topology", className="analysis-section-title"),
                                                html.Label(
                                                    "Analyze fragment",
                                                    htmlFor="topology-site-index",
                                                    className="analysis-label",
                                                ),
                                                dcc.Dropdown(
                                                    id="topology-site-index",
                                                    options=backend.fragment_options(first_state),
                                                    value=first_state.get("topology_site_index"),
                                                    placeholder="(first match of selected species, or click in viewer)",
                                                    clearable=True,
                                                    className="analysis-control",
                                                ),
                                                html.Div(
                                                    "Display tiling and analysis are independent: switch the analysed "
                                                    "fragment here without changing what is drawn.",
                                                    className="analysis-help",
                                                ),
                                                dcc.Graph(
                                                    id="topology-histogram",
                                                    figure=topology_histogram_figure(first_topology),
                                                    className="analysis-graph",
                                                    style={"height": "260px"},
                                                ),
                                                html.Pre(
                                                    id="topology-results",
                                                    children=topology_results_markdown(first_topology),
                                                    className="analysis-results",
                                                ),
                                            ],
                                            className="analysis-section",
                                        ),
                                        html.Section(
                                            [
                                                html.Div("Polyhedra", className="analysis-section-title"),
                                                dcc.Checklist(
                                                    id="topology-toggle",
                                                    options=[{"label": "Show polyhedra overlay", "value": "enabled"}],
                                                    value=["enabled"] if first_state.get("topology_enabled", False) else [],
                                                    style={"display": "inline-block", "marginTop": "4px", "marginBottom": "4px"},
                                                ),
                                                html.Div(
                                                    "Each row defines one MolCrysKit molecule-level packing polyhedron: "
                                                    "centre species + explicit ligand species + colour. The overlay "
                                                    "tiles every matching site in the structure.",
                                                    style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                                                ),
                                                html.Div(
                                                    [
                                                        html.Button(
                                                            "+ Add",
                                                            id="polyhedra-add-btn",
                                                            n_clicks=0,
                                                            style={
                                                                "fontSize": "12px",
                                                                "padding": "2px 8px",
                                                                "verticalAlign": "middle",
                                                                "cursor": "pointer",
                                                            },
                                                            title="Add a named polyhedron row (centre + explicit ligand restriction + colour).",
                                                        ),
                                                    ],
                                                    style={"marginTop": "8px"},
                                                ),
                                                html.Div(
                                                    id="polyhedra-rows-container",
                                                    children=_polyhedra_table_rows(
                                                        first_state.get("polyhedron_specs") or [],
                                                        backend.species_options(first_state["structure"]),
                                                    ),
                                                    style={"marginTop": "6px"},
                                                ),
                                            ],
                                            className="analysis-section",
                                        ),
                                        html.Section(
                                            [
                                                html.Div("BFDH Morphology", className="analysis-section-title"),
                                                html.Div(
                                                    "Simulate crystal morphology using the Bravais-Friedel-Donnay-Harker (BFDH) method.",
                                                    style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label("Max Index", style={"fontSize": "11px", "marginRight": "4px"}),
                                                        dcc.Input(
                                                            id="bfdh-max-index",
                                                            type="number",
                                                            min=1,
                                                            max=5,
                                                            step=1,
                                                            value=2,
                                                            style={"width": "40px", "fontSize": "11px", "marginRight": "12px"},
                                                        ),
                                                        html.Label("Top N", style={"fontSize": "11px", "marginRight": "4px"}),
                                                        dcc.Input(
                                                            id="bfdh-top-n",
                                                            type="number",
                                                            min=1,
                                                            max=50,
                                                            step=1,
                                                            value=10,
                                                            style={"width": "40px", "fontSize": "11px", "marginRight": "12px"},
                                                        ),
                                                        html.Button(
                                                            "Run BFDH",
                                                            id="bfdh-run-btn",
                                                            n_clicks=0,
                                                            style={
                                                                "fontSize": "12px",
                                                                "padding": "2px 8px",
                                                                "cursor": "pointer",
                                                            },
                                                        ),
                                                    ],
                                                    style={"display": "flex", "alignItems": "center", "marginTop": "8px"},
                                                ),
                                                dcc.Loading(
                                                    id="bfdh-loading",
                                                    type="dot",
                                                    color="#2f6df6",
                                                    children=html.Div(
                                                        id="bfdh-results-container",
                                                        style={"marginTop": "8px", "fontSize": "11px"},
                                                    ),
                                                ),
                                                html.Div(
                                                    [
                                                        dcc.Checklist(
                                                            id="bfdh-morphology-enabled",
                                                            options=[{"label": " Show 3D Morphology", "value": "enabled"}],
                                                            value=["enabled"],
                                                            style={"fontSize": "11px"},
                                                        ),
                                                    ],
                                                    style={"marginTop": "8px"},
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label("Scale", style={"fontSize": "11px", "width": "40px"}),
                                                        dcc.Slider(
                                                            id="bfdh-morphology-scale",
                                                            min=0.5,
                                                            max=5.0,
                                                            step=0.1,
                                                            value=1.0,
                                                            marks={0.5: "0.5x", 1: "1x", 2: "2x", 5: "5x"},
                                                        ),
                                                    ],
                                                    style={"marginTop": "8px"},
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label("Opacity", style={"fontSize": "11px", "width": "40px"}),
                                                        dcc.Slider(
                                                            id="bfdh-morphology-opacity",
                                                            min=0.1,
                                                            max=1.0,
                                                            step=0.1,
                                                            value=0.8,
                                                            marks={0.1: "0.1", 0.5: "0.5", 1.0: "1.0"},
                                                        ),
                                                    ],
                                                    style={"marginTop": "8px"},
                                                ),
                                                html.Div(
                                                    [
                                                        html.Label("Color", style={"fontSize": "11px", "marginRight": "8px"}),
                                                        dcc.Input(
                                                            id="bfdh-morphology-color",
                                                            type="color",
                                                            value="#4f7cff",
                                                            debounce=False,
                                                            style={
                                                                "width": "36px",
                                                                "height": "24px",
                                                                "padding": "0",
                                                                "border": "1px solid #BBB",
                                                            },
                                                        ),
                                                    ],
                                                    style={"marginTop": "8px", "display": "flex", "alignItems": "center"},
                                                ),
                                            ],
                                            className="analysis-section",
                                        ),
                                    ],
                                    id="analysis-panel-content",
                                    className="analysis-tab-content",
                                ),
                                html.Div(
                                    [
                                        _operation_panel_section(disorder_resolve),
                                        html.Section(
                                            [
                                                html.Div("Display Transforms", className="analysis-section-title"),
                                                html.Div(
                                                    "Transforms run top → bottom; each sees the previous one’s output. "
                                                    "Seed format: ‘all’, ‘elem:Pb,Cl’, ‘label:Pb1’, ‘index:0,5’, "
                                                    "‘frag:A0’. Bare ‘Pb,Cl’ = elements.",
                                                    style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                                                ),
                                                html.Div(
                                                    [
                                                        dcc.Dropdown(
                                                            id="transforms-kind-select",
                                                            options=[
                                                                {"label": label, "value": kind}
                                                                for kind, label in _TRANSFORM_KIND_NAMES.items()
                                                            ],
                                                            value="repeat",
                                                            clearable=False,
                                                            style={"width": "140px", "fontSize": "12px", "display": "inline-block", "marginRight": "4px"},
                                                        ),
                                                        html.Button(
                                                            "+ Add",
                                                            id="transforms-add-btn",
                                                            n_clicks=0,
                                                            style={
                                                                "fontSize": "12px",
                                                                "padding": "2px 8px",
                                                                "verticalAlign": "middle",
                                                                "cursor": "pointer",
                                                            },
                                                            title="Append a new transform of the selected kind. Default params = a sane no-op.",
                                                        ),
                                                    ],
                                                    style={"display": "flex", "alignItems": "center", "gap": "4px", "marginTop": "6px"},
                                                ),
                                                html.Div(
                                                    [
                                                        html.Button(
                                                            "2×2×2",
                                                            id="transforms-preset-2x",
                                                            n_clicks=0,
                                                            style={"fontSize": "11px", "padding": "2px 6px", "marginRight": "4px", "cursor": "pointer"},
                                                            title="Quick preset: append a repeat 2×2×2 (or replace the existing repeat).",
                                                        ),
                                                        html.Button(
                                                            "3×3×3",
                                                            id="transforms-preset-3x",
                                                            n_clicks=0,
                                                            style={"fontSize": "11px", "padding": "2px 6px", "marginRight": "4px", "cursor": "pointer"},
                                                            title="Quick preset: repeat 3×3×3.",
                                                        ),
                                                        html.Button(
                                                            "Home cell",
                                                            id="transforms-clear-repeat",
                                                            n_clicks=0,
                                                            style={"fontSize": "11px", "padding": "2px 6px", "marginRight": "4px", "cursor": "pointer"},
                                                            title="Drop any repeat transform (back to single home cell).",
                                                        ),
                                                        html.Button(
                                                            "Clear all",
                                                            id="transforms-clear-btn",
                                                            n_clicks=0,
                                                            style={"fontSize": "11px", "padding": "2px 6px", "cursor": "pointer", "color": "#A00"},
                                                            title="Drop every transform (back to the raw scene).",
                                                        ),
                                                    ],
                                                    style={"marginTop": "6px"},
                                                ),
                                                html.Div(
                                                    id="transforms-rows-container",
                                                    children=_transforms_table_rows(first_state.get("transforms") or []),
                                                    style={"marginTop": "6px"},
                                                ),
                                            ],
                                            className="analysis-section operation-section",
                                        ),
                                    ],
                                    id="operation-panel-content",
                                    className="analysis-tab-content analysis-tab-content--hidden",
                                ),
                            ],
                            className="analysis-panel-body",
                        ),
                    ],
                    id="right-panel",
                    className="analysis-panel analysis-panel--collapsed",
                    style={
                        "width": "320px",
                        "minWidth": "260px",
                        "maxWidth": "640px",
                        "flex": "0 0 auto",
                        "padding": "16px",
                        "borderLeft": "1px solid #DDDDDD",
                        "backgroundColor": "#FAFAFA",
                        "height": "100vh",
                        "overflowY": "auto",
                    },
                ),
                # Floating "Server log" panel (bottom-right). Polls
                # ``/api/v1/perf`` every second to show the user which
                # callbacks fired and how long each one took. Collapsed by
                # default to keep the UI clean; click the header to
                # expand. Lives outside the right-panel so the analysis
                # column can be hidden without losing the perf signal.
                html.Div(
                    [
                        html.Div(
                            [
                                html.Button(
                                    "Server log ▾",
                                    id="perf-log-toggle",
                                    n_clicks=0,
                                    className="perf-log-toggle",
                                ),
                                html.Button(
                                    "Clear",
                                    id="perf-log-clear",
                                    n_clicks=0,
                                    className="perf-log-clear",
                                ),
                            ],
                            className="perf-log-header",
                        ),
                        html.Div(
                            id="perf-log-body",
                            className="perf-log-body",
                            children=[
                                html.Div(
                                    "Waiting for events… (interact with the UI to see callbacks)",
                                    className="perf-log-empty",
                                )
                            ],
                        ),
                        dcc.Interval(id="perf-log-poll", interval=1000, n_intervals=0),
                        dcc.Store(id="perf-log-cursor", data={"seq": 0, "events": []}),
                    ],
                    id="perf-log-panel",
                    className="perf-log-panel perf-log-panel--collapsed",
                ),
            ],
            id="viewer-root",
            style={
                "display": "flex",
                "height": "100vh",
                "overflow": "hidden",
                "backgroundColor": "#FFFFFF",
            },
        )

    app.layout = _serve_layout


    register_state_callbacks(app, backend)
    register_editor_callbacks(app, backend)
    register_analysis_callbacks(app, backend)
    register_operations_callbacks(app, backend)
    register_disorder_callbacks(app, backend)
    register_view_callbacks(app, backend)
    register_api(app, backend)
    if str(os.environ.get("MATTERVIS_PREWARM", "1")).lower() not in {"0", "false", "no", "off"}:
        _start_cache_prewarm(backend)
    if str(os.environ.get("MATTERVIS_AUDIT", "0")).lower() in {"1", "true", "yes", "on"}:
        _install_callback_audit(app)
    return app


def _build_parser():
    parser = argparse.ArgumentParser(description="Standalone crystal viewer with topology analysis.")
    parser.add_argument("--preset", default=DEFAULT_PRESET_PATH, help="Preset JSON to load and save.")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind.")
    parser.add_argument("--port", type=int, default=50001, help="Port to expose.")
    parser.add_argument("--structure", nargs="*", help="Serve only selected catalog structure(s).")
    parser.add_argument(
        "--cif",
        action="append",
        default=[],
        help="Optional CIF path to preload. Repeat the flag to preload multiple files: --cif a.cif --cif b.cif.",
    )
    parser.add_argument("--api-only", action="store_true", help="Reserved for automation mode; still serves the same app.")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    app = create_app(args.preset, names=args.structure, root_dir=WORKSPACE_DIR, cif_paths=args.cif or [])
    print(f"Serving crystal viewer at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
