from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .app_shared import *
from .app_normalizers import *

def _seed_selector_to_text(seeds: dict[str, Any] | None) -> str:
    if not isinstance(seeds, dict) or not seeds:
        return ""
    if seeds.get("all"):
        return "all"
    if seeds.get("elements"):
        return "elem:" + ",".join(str(x) for x in seeds["elements"])
    if seeds.get("labels"):
        return "label:" + ",".join(str(x) for x in seeds["labels"])
    if seeds.get("atom_indices"):
        return "index:" + ",".join(str(x) for x in seeds["atom_indices"])
    if seeds.get("fragment_labels"):
        return "frag:" + ",".join(str(x) for x in seeds["fragment_labels"])
    return ""


def _seed_text_to_selector(text: Any) -> dict[str, Any]:
    if text is None:
        return {}
    raw = str(text).strip()
    if not raw:
        return {}
    if raw.lower() == "all":
        return {"all": True}
    if ":" in raw:
        prefix, rest = raw.split(":", 1)
        prefix = prefix.strip().lower()
        values = [v.strip() for v in rest.split(",") if v.strip()]
        if not values:
            return {}
        if prefix in ("elem", "element", "elements", "el"):
            return {"elements": values}
        if prefix in ("label", "labels", "lab"):
            return {"labels": values}
        if prefix in ("index", "indices", "idx", "atom_index"):
            try:
                return {"atom_indices": [int(v) for v in values]}
            except ValueError:
                return {}
        if prefix in ("frag", "fragment", "fragment_labels"):
            return {"fragment_labels": values}
        return {}
    # No prefix: treat as element list (common AI / quick-typing case).
    values = [v.strip() for v in raw.split(",") if v.strip()]
    return {"elements": values} if values else {}


def _transform_param_widgets(transform: dict[str, Any]) -> list[Any]:
    """Build the per-kind parameter widgets for one transform row.

    All widgets carry ``{type: "trf-param-<field>", transform_id: ...}``
    ids so the parent callback can identify them. The set of widgets is
    chosen per ``kind``; absent fields render as nothing so the row
    height stays predictable per transform kind.
    """
    from dash import dcc, html

    transform_id = transform["id"]
    kind = transform.get("kind") or "repeat"
    params = transform.get("params") or {}
    children: list[Any] = []
    if kind == "repeat":
        for axis in ("a", "b", "c"):
            children.append(
                html.Span(axis, style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"})
            )
            children.append(
                dcc.Input(
                    id={"type": f"trf-param-{axis}", "transform_id": transform_id},
                    type="number",
                    min=1,
                    step=1,
                    value=int(params.get(axis, 1) or 1),
                    style={"width": "50px", "fontSize": "12px"},
                    debounce=True,
                )
            )
    elif kind in ("grow_radius", "grow_bonds", "complete_fragment", "complete_polyhedron", "by_symmetry"):
        children.append(
            html.Span("seeds", style={"fontSize": "11px", "color": "#666", "marginRight": "4px"})
        )
        children.append(
            dcc.Input(
                id={"type": "trf-param-seeds", "transform_id": transform_id},
                type="text",
                value=_seed_selector_to_text(params.get("seeds")),
                placeholder="elem:Pb  /  label:Pb1  /  all",
                style={"flex": "1", "minWidth": "100px", "fontSize": "12px"},
                debounce=True,
            )
        )
        if kind == "grow_radius":
            children.extend([
                html.Span("\u00c5", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-radius", "transform_id": transform_id},
                    type="number",
                    min=0.0,
                    step=0.1,
                    value=float(params.get("radius", 0.0) or 0.0),
                    style={"width": "60px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "grow_bonds":
            children.extend([
                html.Span("hops", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-hops", "transform_id": transform_id},
                    type="number",
                    min=0,
                    step=1,
                    value=int(params.get("hops", 1) or 1),
                    style={"width": "50px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "complete_fragment":
            children.extend([
                html.Span("max hops", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-maxhops", "transform_id": transform_id},
                    type="number",
                    min=1,
                    step=1,
                    value=int(params.get("max_hops", 32) or 32),
                    style={"width": "50px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "complete_polyhedron":
            children.extend([
                html.Span("cutoff \u00c5", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
                dcc.Input(
                    id={"type": "trf-param-cutoff", "transform_id": transform_id},
                    type="number",
                    min=0.0,
                    step=0.1,
                    value=float(params.get("cutoff", 4.0) or 4.0),
                    style={"width": "60px", "fontSize": "12px"},
                    debounce=True,
                ),
            ])
        elif kind == "by_symmetry":
            # JSON ops textarea -- power-user / AI path. Empty = no ops.
            import json as _json
            ops_json = _json.dumps(params.get("ops") or [])
            children.append(
                dcc.Textarea(
                    id={"type": "trf-param-ops", "transform_id": transform_id},
                    value=ops_json,
                    placeholder='[[[[r11,r12,r13],[r21,r22,r23],[r31,r32,r33]],[tx,ty,tz]], ...]',
                    style={"width": "100%", "minHeight": "40px", "fontSize": "11px", "fontFamily": "monospace", "marginTop": "4px"},
                ),
            )
    elif kind == "slab":
        miller = params.get("miller") or [1, 0, 0]
        for i, axis in enumerate(("h", "k", "l")):
            children.append(
                html.Span(axis, style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"})
            )
            children.append(
                dcc.Input(
                    id={"type": f"trf-param-miller-{i}", "transform_id": transform_id},
                    type="number",
                    step=1,
                    value=int(miller[i] if i < len(miller) else 0),
                    style={"width": "44px", "fontSize": "12px"},
                    debounce=True,
                )
            )
        children.extend([
            html.Span("layers", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
            dcc.Input(
                id={"type": "trf-param-layers", "transform_id": transform_id},
                type="number",
                min=1,
                step=1,
                value=int(params.get("layers") or 3),
                style={"width": "50px", "fontSize": "12px"},
                debounce=True,
            ),
            html.Span("vacuum \u00c5", style={"fontSize": "11px", "color": "#666", "margin": "0 2px 0 6px"}),
            dcc.Input(
                id={"type": "trf-param-vacuum", "transform_id": transform_id},
                type="number",
                min=0.0,
                step=0.5,
                value=float(params.get("vacuum", 10.0) or 10.0),
                style={"width": "60px", "fontSize": "12px"},
                debounce=True,
            ),
        ])
    return children


def _transforms_table_rows(transforms: list[dict[str, Any]]):
    """One row per transform spec. Each row carries the kind label,
    enabled/delete controls, and a kind-specific parameter line."""
    from dash import dcc, html

    if not transforms:
        return [
            html.Div(
                "No transforms. Use the Add menu below to repeat the cell, grow by radius, slab, ...",
                style={"fontSize": "12px", "color": "#777", "margin": "6px 0"},
            )
        ]
    rows = []
    for index, transform in enumerate(transforms):
        kind = transform.get("kind") or "repeat"
        rows.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(
                                f"{index + 1}.",
                                style={"fontSize": "11px", "color": "#888", "marginRight": "4px", "minWidth": "16px"},
                            ),
                            html.Span(
                                _TRANSFORM_KIND_NAMES.get(kind, kind),
                                style={
                                    "fontSize": "12px",
                                    "fontWeight": "bold",
                                    "color": "#444",
                                    "marginRight": "6px",
                                    "flex": "1",
                                },
                            ),
                            dcc.Checklist(
                                id={"type": "trf-row-enabled", "transform_id": transform["id"]},
                                options=[{"label": "", "value": "yes"}],
                                value=["yes"] if transform.get("enabled", True) else [],
                                style={"display": "inline-block", "marginLeft": "4px"},
                            ),
                            html.Button(
                                "\u25b2",
                                id={"type": "trf-row-up", "transform_id": transform["id"]},
                                n_clicks=0,
                                disabled=index == 0,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#666",
                                    "padding": "0 4px",
                                    "cursor": "pointer" if index > 0 else "not-allowed",
                                    "lineHeight": "18px",
                                    "borderRadius": "3px",
                                    "marginLeft": "2px",
                                    "fontSize": "11px",
                                },
                                title="Move earlier in the pipeline",
                            ),
                            html.Button(
                                "\u25bc",
                                id={"type": "trf-row-down", "transform_id": transform["id"]},
                                n_clicks=0,
                                disabled=index >= len(transforms) - 1,
                                style={
                                    "background": "transparent",
                                    "border": "1px solid #DDD",
                                    "color": "#666",
                                    "padding": "0 4px",
                                    "cursor": "pointer" if index < len(transforms) - 1 else "not-allowed",
                                    "lineHeight": "18px",
                                    "borderRadius": "3px",
                                    "marginLeft": "2px",
                                    "fontSize": "11px",
                                },
                                title="Move later in the pipeline",
                            ),
                            html.Button(
                                "\u00d7",
                                id={"type": "trf-row-delete", "transform_id": transform["id"]},
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
                                title="Remove this transform",
                            ),
                        ],
                        style={"display": "flex", "alignItems": "center"},
                    ),
                    html.Div(
                        _transform_param_widgets(transform),
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "flexWrap": "wrap",
                            "gap": "2px",
                            "marginTop": "4px",
                        },
                    ),
                ],
                style={
                    "padding": "6px 4px",
                    "marginBottom": "6px",
                    "border": "1px solid #EEE",
                    "borderRadius": "4px",
                    "background": "#F8F8FB",
                },
            )
        )
    return rows


# Dispatch table for right-click + keyboard actions. The Dash callback
# resolves which mutation to run; this helper does the actual backend
# calls so the callback stays a thin shim. ``target`` is the full
# rightclick-target store payload; ``payload`` is shorthand for
# ``target.get("payload")``. Optional kwargs (``color``, ``radius``,
# ``hops``) are passed through from the popover / keyboard layer.

__all__ = [name for name in globals() if not name.startswith("__")]
