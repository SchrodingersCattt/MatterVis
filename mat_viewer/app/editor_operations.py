from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *


def _operation_count(value: Any) -> int:
    try:
        return max(1, min(int(value), 128))
    except (TypeError, ValueError):
        return 5


def _operation_panel_section(disorder_resolve: dict[str, Any] | None = None):
    disorder_resolve = disorder_resolve or {}
    return html.Section(
        [
            html.Div("Operation", className="analysis-section-title"),
            html.Div(
                [
                    html.Div("Resolve disorder", className="operation-subsection-title"),
                    html.Label("Mode", htmlFor="disorder-resolve-method", className="analysis-label"),
                    dcc.Dropdown(
                        id="disorder-resolve-method",
                        options=[
                            {"label": "Optimal (1)", "value": "optimal"},
                            {"label": "Enumerate (top-N)", "value": "enumerate"},
                            {"label": "Random sample", "value": "random"},
                        ],
                        value=str(disorder_resolve.get("method") or "enumerate"),
                        clearable=False,
                        className="analysis-control",
                    ),
                    html.Div(
                        [
                            html.Label(
                                "Count",
                                htmlFor="disorder-resolve-count",
                                className="analysis-label operation-inline-label",
                            ),
                            dcc.Input(
                                id="disorder-resolve-count",
                                type="number",
                                min=1,
                                step=1,
                                value=_operation_count(disorder_resolve.get("count")),
                                debounce=True,
                                className="operation-number-input",
                            ),
                            html.Label(
                                "Seed",
                                htmlFor="disorder-resolve-seed",
                                className="analysis-label operation-inline-label",
                            ),
                            dcc.Input(
                                id="disorder-resolve-seed",
                                type="number",
                                step=1,
                                value=disorder_resolve.get("seed"),
                                placeholder="random",
                                debounce=True,
                                className="operation-number-input",
                            ),
                        ],
                        className="operation-control-row",
                    ),
                    html.Button("Resolve", id="disorder-resolve-btn", n_clicks=0, className="operation-button"),
                    dcc.Loading(
                        id="disorder-replicas-loading",
                        type="dot",
                        color="#2f6df6",
                        children=html.Div(
                            id="disorder-replicas-list",
                            className="disorder-replicas-list",
                            children=[
                                html.Div(
                                    "Run Resolve to list ordered disorder replicas.",
                                    className="disorder-empty",
                                )
                            ],
                        ),
                    ),
                ],
                className="operation-subsection",
            ),
        ],
        className="analysis-section operation-section",
    )


__all__ = [name for name in globals() if not name.startswith("__")]
