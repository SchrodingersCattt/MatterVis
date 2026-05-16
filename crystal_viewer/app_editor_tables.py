from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .app_shared import *
from .app_normalizers import *

def _perf_log_row(entry: dict[str, Any]) -> Any:
    """Render one perf-log event as a Dash row.

    Layout: ``[hh:mm:ss.mmm] [label] [duration ms] [info kv pairs]``
    The duration cell is coloured green / amber / red based on
    ``_PERF_FAST_MS`` / ``_PERF_SLOW_MS`` so slow events pop out
    visually.
    """
    iso = entry.get("iso", "")
    clock = iso.split("T", 1)[1] if "T" in iso else iso
    label = entry.get("label", "")
    ms = entry.get("ms")
    if ms is None:
        ms_text = ""
        ms_class = "perf-log-ms perf-log-ms--none"
    else:
        ms_text = f"{ms:6.1f} ms"
        if ms < _PERF_FAST_MS:
            ms_class = "perf-log-ms perf-log-ms--fast"
        elif ms < _PERF_SLOW_MS:
            ms_class = "perf-log-ms perf-log-ms--mid"
        else:
            ms_class = "perf-log-ms perf-log-ms--slow"
    info = entry.get("info") or {}
    info_pairs = []
    for key, value in info.items():
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value[:3]) + ("…" if len(value) > 3 else "")
        text = str(value)
        if len(text) > 36:
            text = text[:33] + "…"
        info_pairs.append(f"{key}={text}")
    info_text = " ".join(info_pairs)
    return html.Div(
        [
            html.Span(clock, className="perf-log-clock"),
            html.Span(label, className="perf-log-label"),
            html.Span(ms_text, className=ms_class),
            html.Span(info_text, className="perf-log-info"),
        ],
        className="perf-log-row",
    )


def _polyhedra_table_rows(
    specs: list[dict[str, Any]],
    species_options: list[dict[str, Any]],
):
    """Build one row of dash inputs per polyhedron spec.

    Each row id is pattern-matched ``{type, spec_id}`` so a single
    ALL-input callback can react to any inline edit and a MATCH/ALL
    callback can identify the deleted row via
    ``callback_context.triggered_id``.
    """
    from dash import dcc, html

    if not specs:
        return [
            html.Div(
                "No named polyhedra. Click \u201cAdd\u201d to register one (centre + optional ligand).",
                className="polyhedra-empty",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    ligand_options = [{"label": "(auto)", "value": _AUTO_LIGAND_VALUE}] + list(species_options)
    rows = []
    for spec in specs:
        shell_mode = (
            _POLY_SHELL_MODE_ENCLOSURE
            if spec.get("enforce_enclosure", True)
            else _POLY_SHELL_MODE_GAP
        )
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            dcc.Input(
                                id={"type": "poly-row-color", "spec_id": spec["id"]},
                                type="color",
                                value=str(spec.get("color") or "#7C5CBF"),
                                style={
                                    "width": "30px",
                                    "height": "26px",
                                    "padding": "0",
                                    "border": "1px solid #BBB",
                                    "verticalAlign": "middle",
                                },
                                debounce=False,
                            ),
                            dcc.Dropdown(
                                id={"type": "poly-row-center", "spec_id": spec["id"]},
                                options=species_options,
                                value=str(spec.get("center_species") or ""),
                                clearable=False,
                                style={"flex": "1", "minWidth": "70px", "fontSize": "12px"},
                            ),
                            html.Span("->", style={"color": "#888", "fontSize": "12px"}),
                            dcc.Dropdown(
                                id={"type": "poly-row-ligand", "spec_id": spec["id"]},
                                options=ligand_options,
                                value=str(spec.get("ligand_species") or _AUTO_LIGAND_VALUE),
                                clearable=False,
                                style={"flex": "1", "minWidth": "70px", "fontSize": "12px"},
                            ),
                            dcc.Checklist(
                                id={"type": "poly-row-enabled", "spec_id": spec["id"]},
                                options=[{"label": "", "value": "yes"}],
                                value=["yes"] if spec.get("enabled", True) else [],
                                style={"display": "inline-block", "marginLeft": "4px"},
                            ),
                            html.Button(
                                "x",
                                id={"type": "poly-row-delete", "spec_id": spec["id"]},
                                n_clicks=0,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#A00",
                                    "padding": "0 8px",
                                    "cursor": "pointer",
                                    "lineHeight": "20px",
                                    "borderRadius": "3px",
                                },
                                title="Remove this polyhedron row",
                            ),
                        ],
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "gap": "4px",
                        },
                    ),
                    html.Details(
                        [
                            html.Summary(
                                "Packing shell options",
                                style={"cursor": "pointer", "fontSize": "10px", "color": "#666"},
                            ),
                            html.Div(
                                [
                                    html.Label("Shell closure", style={"fontSize": "10px", "color": "#555"}),
                                    dcc.Dropdown(
                                        id={"type": "poly-row-shell-mode", "spec_id": spec["id"]},
                                        options=[
                                            {"label": "Gap + enclosure", "value": _POLY_SHELL_MODE_ENCLOSURE},
                                            {"label": "Gap only", "value": _POLY_SHELL_MODE_GAP},
                                        ],
                                        value=shell_mode,
                                        clearable=False,
                                        style={"fontSize": "11px"},
                                    ),
                                    html.Label(
                                        "Centering tolerance",
                                        style={"fontSize": "10px", "color": "#555", "marginTop": "4px"},
                                    ),
                                    dcc.Input(
                                        id={"type": "poly-row-centroid-offset", "spec_id": spec["id"]},
                                        type="number",
                                        min=0,
                                        max=10,
                                        step=0.05,
                                        value=float(spec.get("centroid_offset_frac", DEFAULT_CENTROID_OFFSET_FRAC)),
                                        debounce=True,
                                        placeholder="0.15",
                                        style={"width": "100%", "fontSize": "11px"},
                                    ),
                                ],
                                style={
                                    "display": "grid",
                                    "gridTemplateColumns": "1fr",
                                    "gap": "2px",
                                    "padding": "4px 0 0 34px",
                                },
                            ),
                        ],
                        open=False,
                    ),
                ],
                style={
                    "display": "block",
                    "marginBottom": "4px",
                },
            )
        )
    return rows


_ATOM_GROUP_KIND_ALL = "all"
_ATOM_GROUP_KIND_ELEMENTS = "elements"
_ATOM_GROUP_KIND_MINOR = "minor"
_ATOM_GROUP_KIND_MAJOR = "major"
_ATOM_GROUP_INHERIT = "__inherit__"


def _selector_kind(selector: dict[str, Any]) -> str:
    if selector.get("all"):
        return _ATOM_GROUP_KIND_ALL
    if "is_minor" in selector and "elements" not in selector:
        return _ATOM_GROUP_KIND_MINOR if selector["is_minor"] else _ATOM_GROUP_KIND_MAJOR
    return _ATOM_GROUP_KIND_ELEMENTS


def _selector_elements_text(selector: dict[str, Any]) -> str:
    elements = selector.get("elements") or []
    return ",".join(str(e) for e in elements)


def _atom_groups_table_rows(
    groups: list[dict[str, Any]],
    element_options: list[dict[str, Any]],
):
    """Build one row of dash inputs per atom-group rule. Same
    pattern-match scheme as ``_polyhedra_table_rows``: every input id
    is ``{type, group_id}``.
    """
    from dash import dcc, html

    if not groups:
        return [
            html.Div(
                "No atom-group rules. Use the preset buttons below or click \u201cAdd\u201d to start.",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    rows = []
    for group in groups:
        selector = group.get("selector") or {}
        kind = _selector_kind(selector)
        elements_text = _selector_elements_text(selector)
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            dcc.Checklist(
                                id={"type": "ag-row-visible", "group_id": group["id"]},
                                options=[{"label": "", "value": "yes"}],
                                value=["yes"] if group.get("visible", True) else [],
                                style={"display": "inline-block"},
                            ),
                            dcc.Input(
                                id={"type": "ag-row-color", "group_id": group["id"]},
                                type="color",
                                value=str(group.get("color") or "#888888"),
                                style={
                                    "width": "30px",
                                    "height": "26px",
                                    "padding": "0",
                                    "border": "1px solid #BBB",
                                    "verticalAlign": "middle",
                                    "marginLeft": "4px",
                                },
                                debounce=False,
                            ),
                            dcc.Dropdown(
                                id={"type": "ag-row-kind", "group_id": group["id"]},
                                options=[
                                    {"label": "all atoms", "value": _ATOM_GROUP_KIND_ALL},
                                    {"label": "by element", "value": _ATOM_GROUP_KIND_ELEMENTS},
                                    {"label": "minor only", "value": _ATOM_GROUP_KIND_MINOR},
                                    {"label": "major only", "value": _ATOM_GROUP_KIND_MAJOR},
                                ],
                                value=kind,
                                clearable=False,
                                style={"flex": "1", "marginLeft": "4px", "minWidth": "100px", "fontSize": "12px"},
                            ),
                            dcc.Dropdown(
                                id={"type": "ag-row-elements", "group_id": group["id"]},
                                options=element_options,
                                value=[s for s in elements_text.split(",") if s] if kind == _ATOM_GROUP_KIND_ELEMENTS else [],
                                multi=True,
                                placeholder="Pick elements",
                                style={
                                    "flex": "2",
                                    "marginLeft": "4px",
                                    "minWidth": "120px",
                                    "fontSize": "12px",
                                    "display": "block" if kind == _ATOM_GROUP_KIND_ELEMENTS else "none",
                                },
                            ),
                            html.Button(
                                "\u00d7",
                                id={"type": "ag-row-delete", "group_id": group["id"]},
                                n_clicks=0,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#A00",
                                    "padding": "0 8px",
                                    "cursor": "pointer",
                                    "lineHeight": "20px",
                                    "borderRadius": "3px",
                                    "marginLeft": "4px",
                                },
                                title="Remove this group rule",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "gap": "2px"},
                    ),
                    html.Div(
                        [
                            html.Span("opacity", style={"fontSize": "11px", "color": "#666"}),
                            dcc.Slider(
                                id={"type": "ag-row-opacity", "group_id": group["id"]},
                                min=0.0,
                                max=1.0,
                                step=0.05,
                                value=float(group.get("opacity")) if group.get("opacity") is not None else 1.0,
                                marks={0.0: "0", 0.5: "0.5", 1.0: "1"},
                                tooltip={"placement": "bottom", "always_visible": False},
                                updatemode="mouseup",
                                included=True,
                            ),
                        ],
                        style={"marginTop": "4px", "padding": "0 4px"},
                    ),
                    html.Div(
                        [
                            html.Span("material", style={"fontSize": "11px", "color": "#666", "marginRight": "4px"}),
                            dcc.Dropdown(
                                id={"type": "ag-row-material", "group_id": group["id"]},
                                options=[
                                    {"label": "(scene default)", "value": _ATOM_GROUP_INHERIT},
                                    {"label": "mesh (3D)", "value": "mesh"},
                                    {"label": "flat (2D)", "value": "flat"},
                                ],
                                value=group.get("material") or _ATOM_GROUP_INHERIT,
                                clearable=False,
                                style={"flex": "1", "fontSize": "12px"},
                            ),
                            html.Span("style", style={"fontSize": "11px", "color": "#666", "marginLeft": "8px", "marginRight": "4px"}),
                            dcc.Dropdown(
                                id={"type": "ag-row-style", "group_id": group["id"]},
                                options=[
                                    {"label": "(scene default)", "value": _ATOM_GROUP_INHERIT},
                                    {"label": "ball+stick", "value": "ball_stick"},
                                    {"label": "ball", "value": "ball"},
                                    {"label": "stick", "value": "stick"},
                                    {"label": "ortep", "value": "ortep"},
                                    {"label": "wireframe", "value": "wireframe"},
                                ],
                                value=group.get("style") or _ATOM_GROUP_INHERIT,
                                clearable=False,
                                style={"flex": "1", "fontSize": "12px"},
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "marginTop": "4px", "padding": "0 4px"},
                    ),
                ],
                style={
                    "marginBottom": "8px",
                    "padding": "6px",
                    "border": "1px solid #EEE",
                    "borderRadius": "4px",
                    "background": "#FAFAFA",
                },
            )
        )
    return rows


# --- Phase 4 UI: transforms + bond_groups + polyhedron search supercell ---
#
# These mirror the polyhedra/atom_groups table pattern: each row gets
# pattern-matched ``{type, transform_id|group_id}`` ids so a single
# ALL-input callback handles add/edit/delete dispatching by
# ``callback_context.triggered_id``. The widgets are deliberately minimal --
# the contract is "every backend feature is reachable from the UI", not
# "the UI is pretty". Polished forms can land later; the right-click /
# keyboard layer can also push selectors into the same text inputs used
# here.

_BOND_GROUP_KIND_ALL = "all"
_BOND_GROUP_KIND_BETWEEN = "between"
_BOND_GROUP_KIND_MINOR = "minor"
_BOND_GROUP_KIND_MAJOR = "major"


def _bond_selector_kind(selector: dict[str, Any]) -> str:
    if selector.get("all"):
        return _BOND_GROUP_KIND_ALL
    if selector.get("between_elements"):
        return _BOND_GROUP_KIND_BETWEEN
    if "is_minor" in selector:
        return _BOND_GROUP_KIND_MINOR if selector["is_minor"] else _BOND_GROUP_KIND_MAJOR
    return _BOND_GROUP_KIND_ALL


def _bond_selector_elements_text(selector: dict[str, Any]) -> list[str]:
    return [str(x) for x in (selector.get("between_elements") or [])]


def _bond_groups_table_rows(
    groups: list[dict[str, Any]],
    element_options: list[dict[str, Any]],
):
    """One row of dash inputs per bond-group rule. Same pattern-match
    scheme as ``_atom_groups_table_rows`` but with bond-specific
    selectors (all / between elements / minor / major) and bond-specific
    style fields (color / opacity / radius_scale).
    """
    from dash import dcc, html

    if not groups:
        return [
            html.Div(
                "No bond-group rules. Click \u201cAdd\u201d or right-click a bond to start.",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    rows = []
    for group in groups:
        selector = group.get("selector") or {}
        kind = _bond_selector_kind(selector)
        between_values = _bond_selector_elements_text(selector)
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            dcc.Checklist(
                                id={"type": "bg-row-visible", "group_id": group["id"]},
                                options=[{"label": "", "value": "yes"}],
                                value=["yes"] if group.get("visible", True) else [],
                                style={"display": "inline-block"},
                            ),
                            dcc.Input(
                                id={"type": "bg-row-color", "group_id": group["id"]},
                                type="color",
                                value=str(group.get("color") or _BOND_GROUP_FALLBACK_COLOR),
                                style={
                                    "width": "30px",
                                    "height": "26px",
                                    "padding": "0",
                                    "border": "1px solid #BBB",
                                    "verticalAlign": "middle",
                                    "marginLeft": "4px",
                                },
                                debounce=False,
                            ),
                            dcc.Dropdown(
                                id={"type": "bg-row-kind", "group_id": group["id"]},
                                options=[
                                    {"label": "all bonds", "value": _BOND_GROUP_KIND_ALL},
                                    {"label": "between elements", "value": _BOND_GROUP_KIND_BETWEEN},
                                    {"label": "minor only", "value": _BOND_GROUP_KIND_MINOR},
                                    {"label": "major only", "value": _BOND_GROUP_KIND_MAJOR},
                                ],
                                value=kind,
                                clearable=False,
                                style={"flex": "1", "marginLeft": "4px", "minWidth": "100px", "fontSize": "12px"},
                            ),
                            dcc.Dropdown(
                                id={"type": "bg-row-elements", "group_id": group["id"]},
                                options=element_options,
                                value=between_values if kind == _BOND_GROUP_KIND_BETWEEN else [],
                                multi=True,
                                placeholder="Pick 1\u20132 elements (e.g. Pb, Cl)",
                                style={
                                    "flex": "2",
                                    "marginLeft": "4px",
                                    "minWidth": "120px",
                                    "fontSize": "12px",
                                    "display": "block" if kind == _BOND_GROUP_KIND_BETWEEN else "none",
                                },
                            ),
                            html.Button(
                                "\u00d7",
                                id={"type": "bg-row-delete", "group_id": group["id"]},
                                n_clicks=0,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#A00",
                                    "padding": "0 8px",
                                    "cursor": "pointer",
                                    "lineHeight": "20px",
                                    "borderRadius": "3px",
                                    "marginLeft": "4px",
                                },
                                title="Remove this bond-group rule",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center", "gap": "2px"},
                    ),
                    html.Div(
                        [
                            html.Span("opacity", style={"fontSize": "11px", "color": "#666"}),
                            dcc.Slider(
                                id={"type": "bg-row-opacity", "group_id": group["id"]},
                                min=0.0,
                                max=1.0,
                                step=0.05,
                                value=float(group.get("opacity") if group.get("opacity") is not None else 1.0),
                                marks={0.0: "0", 0.5: "0.5", 1.0: "1"},
                                tooltip={"placement": "bottom", "always_visible": False},
                                updatemode="mouseup",
                                included=True,
                            ),
                        ],
                        style={"marginTop": "4px", "padding": "0 4px"},
                    ),
                    html.Div(
                        [
                            html.Span("radius \u00d7", style={"fontSize": "11px", "color": "#666"}),
                            dcc.Slider(
                                id={"type": "bg-row-radius", "group_id": group["id"]},
                                min=0.1,
                                max=3.0,
                                step=0.1,
                                value=float(group.get("radius_scale") if group.get("radius_scale") is not None else 1.0),
                                marks={0.5: "0.5", 1.0: "1", 2.0: "2"},
                                tooltip={"placement": "bottom", "always_visible": False},
                                updatemode="mouseup",
                                included=True,
                            ),
                        ],
                        style={"marginTop": "4px", "padding": "0 4px"},
                    ),
                ],
                style={
                    "padding": "6px 4px",
                    "marginBottom": "6px",
                    "border": "1px solid #EEE",
                    "borderRadius": "4px",
                    "background": "#FAFAFA",
                },
            )
        )
    return rows


# Seed-selector text format used in the Transforms UI rows. The text input
# accepts (case-insensitive):
#   - ``"all"``                 -> {"all": true}
#   - ``"elem:Pb,Cl"`` / ``"el:Pb"`` -> {"elements": ["Pb","Cl"]}
#   - ``"label:Pb1,Cl3"`` / ``"lab:Pb1"`` -> {"labels": ["Pb1","Cl3"]}
#   - ``"index:0,5"`` / ``"idx:0,5"`` -> {"atom_indices": [0,5]}
#   - ``"frag:A0"`` / ``"fragment:A0"`` -> {"fragment_labels": ["A0"]}
# Bare comma-separated values (no prefix) are treated as ``elements``
# because that is the most common case for AI scripting.

__all__ = [name for name in globals() if not name.startswith("__")]
