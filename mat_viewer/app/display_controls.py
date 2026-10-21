"""Describe view controls without changing scientific or display state."""


def display_option_items(mode, selected=()):
    selected = set(selected or ())
    return [
        {"label": "Labels", "value": "labels"},
        {"label": "Axes", "value": "axes"},
        {"label": "Disorder only", "value": "minor_only"},
        {"label": "Hydrogens", "value": "hydrogens"},
        {"label": "Cell boundary", "value": "unit_cell_box",
         "disabled": mode != "unit_cell" and "unit_cell_box" not in selected},
        {"label": "Wrap into cell", "value": "wrap_into_cell",
         "disabled": mode != "unit_cell" and "wrap_into_cell" not in selected},
    ]


def display_option_hint(mode):
    if mode == "unit_cell":
        return ""
    return "Cell boundary and wrap into cell are available in Unit cell scope."