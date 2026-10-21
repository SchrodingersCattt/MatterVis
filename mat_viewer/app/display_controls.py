"""Describe view controls without changing scientific or display state."""

_HIDDEN_DISPLAY_TOKENS = frozenset({"monochrome"})
_SCOPE_LIMITED_OPTIONS = ("unit_cell_box", "wrap_into_cell")


def display_option_items(mode, selected=()):
    """Checklist options for the Display pane.

    Cell boundary and wrap into cell are interactive only in Unit cell scope.
    Outside that scope both options are disabled. Callers must also pass
    :func:`visible_display_options` as the checklist value so a disabled row
    is not left looking checked. ``selected`` is retained for callers that
    still pass the current value.
    """
    del selected
    return [
        {"label": "Labels", "value": "labels"},
        {"label": "Axes", "value": "axes"},
        {"label": "Disorder only", "value": "minor_only"},
        {"label": "Hydrogens", "value": "hydrogens"},
        {
            "label": "Cell boundary",
            "value": "unit_cell_box",
            "disabled": mode != "unit_cell",
        },
        {
            "label": "Wrap into cell",
            "value": "wrap_into_cell",
            "disabled": mode != "unit_cell",
        },
    ]


def display_option_hint(mode):
    if mode == "unit_cell":
        return ""
    return "Cell boundary and wrap into cell are available in Unit cell scope."


def visible_display_options(mode, selected):
    """Checklist value for ``mode``.

    The stored option list can keep ``unit_cell_box`` and ``wrap_into_cell``
    while another scope is active so returning to Unit cell restores the
    choice. The checkbox itself must be unchecked whenever it is disabled.
    """
    visible = [
        opt
        for opt in (selected or [])
        if opt and opt not in _HIDDEN_DISPLAY_TOKENS
    ]
    if mode != "unit_cell":
        hidden = set(_SCOPE_LIMITED_OPTIONS)
        visible = [opt for opt in visible if opt not in hidden]
    return visible


def reconcile_display_options(mode, incoming, previous_mode, previous_options):
    """Persist display options without letting a disabled checkbox drop a preference.

    Outside Unit cell, Cell boundary and wrap into cell are disabled and shown
    unchecked. A checklist echo that simply omits either token must not forget
    a choice the user turned on. Switching back into Unit cell reveals those
    stored choices. Unchecking while Unit cell is active clears that token.
    """
    incoming = [
        opt
        for opt in (incoming or [])
        if opt and opt not in _HIDDEN_DISPLAY_TOKENS
    ]
    previous = [
        opt
        for opt in (previous_options or [])
        if opt and opt not in _HIDDEN_DISPLAY_TOKENS
    ]
    result = list(incoming)
    for token in _SCOPE_LIMITED_OPTIONS:
        stored = token in previous
        if mode != "unit_cell":
            # An explicit token (API, or the checklist before the scope change
            # strips it) is a real preference. A value that merely omits the
            # token is the disabled checkbox reporting itself unchecked.
            if token in incoming or not stored:
                continue
            result.append(token)
            continue
        if previous_mode != "unit_cell" and stored and token not in incoming:
            result.append(token)
    return result
