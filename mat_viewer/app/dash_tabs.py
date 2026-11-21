"""Installed-Dash contracts for the scene tab strip.

``dcc.Tab.label`` is a string on Dash 2.18.2 through 3.x. Passing a
component raises ``TypeError`` in the component constructor. The viewer
layout catches that and replaces the page with the error screen.

Dash 4.0.0 is the first stable release that accepts a component in
``dcc.Tab.label``. The adapter branches on that public version so both
contracts serve the viewer: Dash 2.18.2 through 3.x keep a string label,
and Dash 4.0.0 and later keep a component label. The boolean is cached
for the process.

The scene catalog message is the label source. HTML projections carry
``data-scene-id`` and ``data-display-name`` for the close row and, on
Dash 4, the component label. ``dcc.Tab`` does not declare ``data-*``,
and this module does not extend that private wildcard list.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from packaging.version import parse as parse_version


@cache
def tab_label_accepts_components() -> bool:
    """Return whether ``dcc.Tab(label=component)`` is legal.

    Dash 4.0.0 is the first stable release that accepts a component in
    ``dcc.Tab.label``. Earlier releases in the supported range reject it
    in the constructor.
    """

    import dash

    return parse_version(dash.__version__) >= parse_version("4.0.0")


def scene_tab_dataset(scene_id: str, label: str) -> dict[str, str]:
    """Catalog projection written onto HTML scene nodes."""

    return {
        "data-scene-id": str(scene_id),
        "data-display-name": str(label),
    }


def html_scene_projection(children: Any, scene_id: str, label: str, **kwargs: Any):
    """Return an HTML span that projects one catalog scene.

    Callers pass this span as a Dash 4 tab label or as a child of
    ``scene-tab-close-row`` when the installed Dash only accepts a
    string label. The span is not a second name authority: the catalog
    message overwrites it.
    """

    from dash import html

    props = dict(kwargs)
    props.update(scene_tab_dataset(scene_id, label))
    return html.Span(children, **props)
