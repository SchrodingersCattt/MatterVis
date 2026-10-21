"""Plain-text models for the canvas status bar and the analysis atom list."""

from __future__ import annotations

from typing import Any

DISPLAY_MODE_LABELS = {
    "formula_unit": "Formula unit",
    "unit_cell": "Unit cell",
    "asymmetric_unit": "Asymmetric unit",
    "cluster": "Isolated cluster",
}

ATOM_LIST_LIMIT = 12


def status_bar_parts(state: dict[str, Any] | None, scene: dict[str, Any] | None) -> tuple[str, str, str]:
    """Return ``(structure label, scope label, atom/bond counts)``."""
    state = state or {}
    scene = scene or {}
    label = (
        state.get("scene_label")
        or scene.get("display_title")
        or scene.get("title")
        or state.get("structure")
        or "No structure"
    )
    mode = DISPLAY_MODE_LABELS.get(str(state.get("display_mode") or ""), "")
    atoms = scene.get("draw_atoms") or []
    bonds = scene.get("bonds") or []
    counts = f"{len(atoms)} atoms · {len(bonds)} bonds" if (atoms or bonds) else ""
    return str(label), mode, counts


def atom_list_model(scene: dict[str, Any] | None, *, limit: int = ATOM_LIST_LIMIT) -> dict[str, Any]:
    """First ``limit`` displayed atoms plus the full count.

    The analysis pane lists a short, readable sample. It does not replace
    the selection model and it does not pretend to measure geometry.
    """
    atoms = list((scene or {}).get("draw_atoms") or [])
    rows = []
    for atom in atoms[: max(0, int(limit))]:
        if not isinstance(atom, dict):
            continue
        element = str(atom.get("elem") or atom.get("species") or "")
        label = str(atom.get("label") or element or "?")
        occupancy = atom.get("occupancy", atom.get("occ"))
        rows.append({"label": label, "element": element, "occupancy": occupancy})
    return {"rows": rows, "total": len(atoms), "limit": limit}


def render_atom_list(scene: dict[str, Any] | None, *, limit: int = ATOM_LIST_LIMIT):
    """Dash nodes for the analysis atom list."""
    from dash import html

    model = atom_list_model(scene, limit=limit)
    if not model["rows"]:
        return [html.Div("No atoms in this view.", className="atom-list-empty")]
    rows = []
    for row in model["rows"]:
        occupancy = row["occupancy"]
        occupancy_text = ""
        if isinstance(occupancy, (int, float)) and float(occupancy) < 0.999:
            occupancy_text = f"{float(occupancy):.2f}"
        rows.append(
            html.Div(
                [
                    html.Span(row["label"], className="atom-list-label"),
                    html.Span(row["element"], className="atom-list-element"),
                    html.Span(occupancy_text, className="atom-list-occ"),
                ],
                className="atom-list-row",
            )
        )
    shown = len(model["rows"])
    if model["total"] > shown:
        rows.append(
            html.Div(
                f"Showing {shown} of {model['total']} atoms",
                className="atom-list-more",
            )
        )
    return rows


def canvas_status_bar(structure: str, scope: str, counts: str):
    """Bottom canvas strip: structure, scope, counts, and the busy slot."""
    from dash import html

    return html.Div(
        [
            html.Span(
                structure,
                id="mv-status-structure",
                className="mv-status-bar__primary",
            ),
            html.Span(scope, id="mv-status-scope", className="mv-status-bar__item"),
            html.Span(counts, id="mv-status-counts", className="mv-status-bar__item"),
            html.Span(
                [
                    html.Span(className="mv-status-spinner"),
                    html.Span("Working…", className="mv-status-busy-text"),
                ],
                id="mv-status-busy",
                className="mv-status-bar__busy is-hidden",
            ),
        ],
        id="mv-status-bar",
        className="mv-status-bar",
        role="status",
    )


def register_workbench_status(app, backend) -> None:
    """Publish the status bar and atom list for the scene that is drawn.

    Counts come from ``scene_for_state``, which follows the current display
    mode and transforms. ``bundle.scene`` is the formula-unit scene from
    load and is shared across callers, so it is not a status source.
    """
    from dash import Input, Output

    @app.callback(
        Output("mv-status-structure", "children"),
        Output("mv-status-scope", "children"),
        Output("mv-status-counts", "children"),
        Output("atom-list", "children"),
        Input("agent-state-store", "data"),
        Input("structure-summary", "children"),
    )
    def refresh_workbench_status(agent_state, _summary):
        scene_id = agent_state.get("scene_id") if isinstance(agent_state, dict) else None
        try:
            state = backend.get_state(scene_id)
            scene = backend.scene_for_state(state)
        except (KeyError, TypeError, ValueError, AttributeError):
            return "No structure", "", "", render_atom_list(None)
        label, mode, counts = status_bar_parts(state, scene)
        return label, mode, counts, render_atom_list(scene)
