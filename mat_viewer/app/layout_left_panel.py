"""Left-side controls for the interactive MatterVis layout."""

from __future__ import annotations

from typing import Any

from .shared import dcc, html
from .camera_helpers import _structure_summary
from .editor_tables import _atom_groups_table_rows, _bond_groups_table_rows
from .style_helpers import (
    _minor_opacity_control_style,
    _minor_opacity_disabled,
    _status_class,
)


def build_left_panel(
    *,
    backend: Any,
    first_state: dict[str, Any],
    first_scene: dict[str, Any],
    property_catalog: dict[str, Any],
    property_spec: dict[str, Any],
    property_range: Any,
    preset_path: str,
):
    return html.Div(
        [
            html.H3("MatterVis", style={"marginTop": "0"}),
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
                            html.Span(
                                "Duplicate tab",
                                className="scene-new-tab-hint",
                            ),
                        ],
                        style={"float": "right"},
                    ),
                ],
                style={"marginBottom": "4px"},
            ),
            dcc.Tabs(
                id="scene-tabs",
                value=first_state.get("scene_id")
                or backend.active_scene_id(),
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
                        value=first_state.get("scene_label")
                        or first_state["structure"],
                        placeholder="Scene label",
                        style={"width": "68%", "marginRight": "6px"},
                    ),
                    html.Button(
                        "Rename", id="scene-rename-btn", n_clicks=0
                    ),
                    html.Button(
                        "Close",
                        id="scene-tab-close-active",
                        n_clicks=0,
                        style={"marginLeft": "6px"},
                    ),
                ],
                style={"marginTop": "8px", "marginBottom": "8px"},
            ),
            html.Div(
                id="structure-summary",
                children=_structure_summary(first_scene),
                style={
                    "marginBottom": "12px",
                    "fontSize": "13px",
                    "color": "#444444",
                },
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
                style={
                    "marginBottom": "12px",
                    "whiteSpace": "pre-wrap",
                    "fontSize": "13px",
                },
            ),
            html.Label("Display Scope"),
            dcc.Dropdown(
                id="display-mode-selector",
                options=[
                    {
                        "label": "Formula unit cluster",
                        "value": "formula_unit",
                    },
                    {"label": "Unit cell", "value": "unit_cell"},
                    {
                        "label": "Asymmetric unit",
                        "value": "asymmetric_unit",
                    },
                    {
                        "label": "Isolated cluster (no PBC)",
                        "value": "cluster",
                    },
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
                    {"label": "Disorder Only", "value": "minor_only"},
                    {"label": "Hydrogens", "value": "hydrogens"},
                    {
                        "label": "Unit Cell Box (unit-cell scope)",
                        "value": "unit_cell_box",
                    },
                    # Phase 3: legacy "Monochrome atoms" toggle
                    # has been replaced by the Atom-Groups
                    # editor below (one-click "Monochrome"
                    # preset). Backend still honours the
                    # ``monochrome`` flag for callers / saved
                    # presets that set it directly.
                ],
                value=[
                    opt
                    for opt in first_state["display_options"]
                    if opt != "monochrome"
                ],
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
                        "a",
                        id="view-align-a",
                        n_clicks=0,
                        className="view-align-btn",
                        title="Look down lattice axis a",
                    ),
                    html.Button(
                        "b",
                        id="view-align-b",
                        n_clicks=0,
                        className="view-align-btn",
                        title="Look down lattice axis b",
                    ),
                    html.Button(
                        "c",
                        id="view-align-c",
                        n_clicks=0,
                        className="view-align-btn",
                        title="Look down lattice axis c",
                    ),
                    html.Button(
                        "a*",
                        id="view-align-astar",
                        n_clicks=0,
                        className="view-align-btn",
                        title="Look down reciprocal axis a*",
                    ),
                    html.Button(
                        "b*",
                        id="view-align-bstar",
                        n_clicks=0,
                        className="view-align-btn",
                        title="Look down reciprocal axis b*",
                    ),
                    html.Button(
                        "c*",
                        id="view-align-cstar",
                        n_clicks=0,
                        className="view-align-btn",
                        title="Look down reciprocal axis c*",
                    ),
                    html.Button(
                        "Reset",
                        id="view-reset",
                        n_clicks=0,
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
                            {
                                "label": "Flat shading (fast 3D)",
                                "value": "flat",
                            },
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
                            {
                                "label": "Outline rings",
                                "value": "outline_rings",
                            },
                            {
                                "label": "Opacity from occ.",
                                "value": "opacity",
                            },
                            {
                                "label": "Dashed bonds",
                                "value": "dashed_bonds",
                            },
                            {
                                "label": "Colour shift",
                                "value": "color_shift",
                            },
                            {"label": "None", "value": "none"},
                        ],
                        value=first_state.get("disorder", "outline_rings"),
                        clearable=False,
                        style={"flex": "1"},
                    ),
                ],
                style={
                    "display": "flex",
                    "gap": "6px",
                    "marginBottom": "10px",
                },
            ),
            html.Label("ORTEP Draw Mode"),
            dcc.Dropdown(
                id="ortep-mode-selector",
                options=[
                    {"label": "Solid ellipsoids", "value": "ortep_solid"},
                    {"label": "Octant shading", "value": "ortep_octant"},
                    {"label": "Publication hatch", "value": "ortep_hatch"},
                ],
                value=first_state.get("ortep_mode", "ortep_solid"),
                clearable=False,
                style={"marginBottom": "10px"},
            ),
            html.Label("Atom Scale"),
            dcc.Slider(
                id="atom-scale-slider",
                min=0.5,
                max=1.8,
                step=0.02,
                value=float(first_state["atom_scale"]),
                marks={0.5: "0.5", 1.0: "1.0", 1.5: "1.5", 1.8: "1.8"},
                tooltip={"placement": "bottom", "always_visible": False},
                updatemode="mouseup",
            ),
            html.Label("Bond Radius"),
            dcc.Slider(
                id="bond-radius-slider",
                min=0.05,
                max=0.40,
                step=0.01,
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
                        min=0.10,
                        max=0.90,
                        step=0.02,
                        value=float(first_state["minor_opacity"]),
                        marks={0.1: "0.1", 0.5: "0.5", 0.9: "0.9"},
                        tooltip={
                            "placement": "bottom",
                            "always_visible": False,
                        },
                        updatemode="mouseup",
                        disabled=_minor_opacity_disabled(
                            first_state.get("disorder", "outline_rings")
                        ),
                    ),
                ],
                id="minor-opacity-control",
                style=_minor_opacity_control_style(
                    first_state.get("disorder", "outline_rings")
                ),
            ),
            html.Label("Axis Scale"),
            dcc.Slider(
                id="axis-scale-slider",
                min=0.05,
                max=0.25,
                step=0.01,
                value=float(first_state["axis_scale"]),
                marks={0.05: "0.05", 0.15: "0.15", 0.25: "0.25"},
                tooltip={"placement": "bottom", "always_visible": False},
                updatemode="mouseup",
            ),
            html.Hr(),
            html.H4("Atom property colour"),
            html.Label("Field"),
            dcc.Dropdown(
                id="property-field-selector",
                options=[
                    {"label": item["field"], "value": item["field"]}
                    for item in property_catalog["fields"]
                ],
                value=list(property_spec.get("fields") or []),
                multi=True,
                placeholder="Element colours",
                style={"marginBottom": "8px"},
            ),
            html.Div(
                [
                    dcc.Dropdown(
                        id="property-reduction-selector",
                        options=[
                            {
                                "label": value.replace("_", " ").title(),
                                "value": value,
                            }
                            for value in (
                                "auto",
                                "scalar",
                                "magnitude",
                                "component",
                                "trace",
                                "mean_normal",
                                "von_mises",
                            )
                        ],
                        value=property_spec.get("reduction", "auto"),
                        clearable=False,
                        style={"flex": "1"},
                    ),
                    dcc.Input(
                        id="property-component-input",
                        type="text",
                        value=property_spec.get("component"),
                        placeholder="component",
                        style={"width": "34%"},
                    ),
                ],
                style={
                    "display": "flex",
                    "gap": "6px",
                    "marginBottom": "8px",
                },
            ),
            html.Label("Colormap"),
            dcc.Input(
                id="property-colormap-input",
                type="text",
                value=property_spec.get("colormap", "viridis"),
                debounce=True,
                style={"width": "100%", "marginBottom": "8px"},
            ),
            dcc.RadioItems(
                id="property-range-mode",
                options=[
                    {"label": "Auto range", "value": "auto"},
                    {"label": "Manual range", "value": "manual"},
                ],
                value="manual" if property_range is not None else "auto",
                inline=True,
            ),
            html.Div(
                [
                    dcc.Input(
                        id="property-range-min",
                        type="number",
                        value=None
                        if property_range is None
                        else property_range[0],
                        placeholder="minimum",
                        style={"width": "49%"},
                    ),
                    dcc.Input(
                        id="property-range-max",
                        type="number",
                        value=None
                        if property_range is None
                        else property_range[1],
                        placeholder="maximum",
                        style={"width": "49%"},
                    ),
                ],
                style={
                    "display": "flex",
                    "gap": "2%",
                    "marginBottom": "8px",
                },
            ),
            html.Div(
                [
                    dcc.Input(
                        id="property-center-input",
                        type="number",
                        value=property_spec.get("center"),
                        placeholder="optional center",
                        style={"width": "60%"},
                    ),
                    dcc.Input(
                        id="property-nan-color-input",
                        type="color",
                        value=str(
                            property_spec.get("nan_color", "#BDBDBD")
                        )[:7],
                        style={"width": "36%"},
                    ),
                ],
                style={
                    "display": "flex",
                    "gap": "4%",
                    "marginBottom": "6px",
                },
            ),
            dcc.Checklist(
                id="property-colorbar-toggle",
                options=[{"label": "Show colour bar", "value": "show"}],
                value=["show"]
                if property_spec.get("show_colorbar", True)
                else [],
            ),
            html.Hr(),
            # ---- Phase 3: Atom groups table ----
            html.Div(
                [
                    html.H4(
                        "Atom groups",
                        style={
                            "display": "inline-block",
                            "marginRight": "8px",
                        },
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
                        style={
                            "fontSize": "12px",
                            "padding": "2px 8px",
                            "marginRight": "4px",
                            "cursor": "pointer",
                        },
                        title="Add an 'all atoms = #000000' rule (replacement for the legacy Monochrome checkbox).",
                    ),
                    html.Button(
                        "Clear all",
                        id="atom-groups-clear-btn",
                        n_clicks=0,
                        style={
                            "fontSize": "12px",
                            "padding": "2px 8px",
                            "cursor": "pointer",
                            "color": "#A00",
                        },
                        title="Drop every atom-group rule for this scene.",
                    ),
                ],
                style={"marginTop": "6px"},
            ),
            html.Div(
                "Tip: to hide hydrogens use the Hydrogens checkbox under Display "
                "Options above; that path also rebuilds bonds correctly. "
                "Atom-group rules tweak per-atom colour / opacity / material.",
                style={
                    "fontSize": "11px",
                    "color": "#777",
                    "marginTop": "4px",
                },
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
                        style={
                            "display": "inline-block",
                            "marginRight": "8px",
                        },
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
                style={
                    "fontSize": "11px",
                    "color": "#777",
                    "marginTop": "4px",
                },
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
            html.Button(
                "Export Static Figure",
                id="export-btn",
                n_clicks=0,
                style={"marginLeft": "8px"},
            ),
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
    )
