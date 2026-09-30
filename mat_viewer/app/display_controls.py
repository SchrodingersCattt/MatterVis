"""Describe view controls without changing scientific or display state."""


def display_option_items(mode, selected=()):
    selected = set(selected or ())
    return [
        {"label": "Labels", "value": "labels"},
        {"label": "Axes", "value": "axes"},
        {"label": "Disorder only", "value": "minor_only"},
        {"label": "Hydrogens", "value": "hydrogens"},
        {"label": "Cell boundary", "value": "unit_cell_box",
         # Saved selected values remain actionable so they can be switched off.
         "disabled": mode != "unit_cell" and "unit_cell_box" not in selected},
    ]


def display_option_hint(mode):
    return "" if mode == "unit_cell" else "Cell boundary is available in Unit cell scope."