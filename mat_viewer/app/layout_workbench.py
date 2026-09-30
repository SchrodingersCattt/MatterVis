"""Compose native controls into a workbench before Dash serves the layout.

No controls are recreated or conditionally mounted. The legacy right-panel ID
remains a nested tool container, not an independently sized root column.
"""

from dash import html


def assemble_workbench(root):
    """Place common scene tools, mounted tool panes, and viewer diagnostics."""
    panels = {
        getattr(child, "id", None): child for child in root.children
        if isinstance(getattr(child, "id", None), str)
    }
    sidebar = panels["left-panel"]
    tools = panels["right-panel"]
    viewer = panels["center-panel"]
    log = panels["perf-log-panel"]
    children = sidebar.children
    common_end = next(
        index + 1 for index, child in enumerate(children)
        if getattr(child, "id", None) == "upload-status"
    )
    tabs = html.Div(
        [
            html.Button(
                label, id=f"{tab}-panel-toggle", n_clicks=0, role="tab",
                tabIndex=0 if tab == "display" else -1,
                className="analysis-panel-toggle" + (
                    " analysis-panel-toggle--active" if tab == "display" else ""
                ),
                **{"aria-label": f"{label} tools",
                   "aria-controls": f"{tab}-panel-content",
                   "aria-selected": str(tab == "display").lower(),
                   "aria-pressed": str(tab == "display").lower()},
            )
            for tab, label in (
                ("display", "Display"), ("analysis", "Analysis"),
                ("operation", "Operations"),
            )
        ],
        className="workbench-tabs", role="tablist",
        **{"aria-label": "Structure tools"},
    )
    sidebar.children = [
        html.Div(children[:common_end], className="workbench-common"),
        tabs,
        html.Div(
            children[common_end:], id="display-panel-content",
            className="analysis-tab-content", role="tabpanel",
            **{"aria-labelledby": "display-panel-toggle"},
        ),
        tools,
    ]
    viewer.children.append(log)
    root.children = [
        child for child in root.children if child is not tools and child is not log
    ]
    return root