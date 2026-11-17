"""Installed-Dash contracts for the scene tab strip.

``dcc.Tab.label`` is a string on Dash 2.18.2 through 3.x. Passing a
component raises ``TypeError`` in the component constructor. The viewer
layout catches that and replaces the page with the error screen.

Dash 4.0.0 is the first stable release that accepts a component in
``dcc.Tab.label``. The adapter branches on that public version so both
contracts serve the viewer: Dash 2.18.2 through 3.x keep a string label,
and Dash 4.0.0 and later keep a component label. The boolean is cached
for the process.

Whichever shape is chosen, the tab node carries the scene catalog on
``data-scene-id`` and ``data-display-name``. Tab text is a projection of
that catalog state.
"""

from __future__ import annotations

from functools import cache

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
    """Catalog identity written onto every scene tab node."""

    return {
        "data-scene-id": str(scene_id),
        "data-display-name": str(label),
    }


def apply_scene_tab_dataset(component, scene_id: str, label: str):
    """Attach catalog identity attributes to one tab node.

    ``html`` nodes already accept ``data-*``. ``dcc.Tab`` does not declare
    those props, so the same keys are attached after construction and
    included when the component is serialized. Close-control ids are left
    unchanged.
    """

    attrs = scene_tab_dataset(scene_id, label)
    wildcards = list(getattr(component, "_valid_wildcard_attributes", []) or [])
    if "data-" not in wildcards:
        component._valid_wildcard_attributes = [*wildcards, "data-"]
    for key, value in attrs.items():
        setattr(component, key, value)
    return component
