"""Installed-Dash contracts for the scene tab strip.

``dcc.Tab.label`` is a string on Dash 2.18.2 through 3.x. Passing a
component raises ``TypeError`` in the component constructor. The viewer
layout catches that and replaces the page with the error screen.

Dash 4.0 adds ``label`` to ``Tab._base_nodes`` and accepts a component
there. Read that attribute instead of comparing version strings: it is
the constructor gate the installed package actually enforces.
"""

from __future__ import annotations


def tab_label_accepts_components() -> bool:
    """Return whether ``dcc.Tab(label=component)`` is legal."""

    from dash import dcc

    return "label" in getattr(dcc.Tab, "_base_nodes", ())
