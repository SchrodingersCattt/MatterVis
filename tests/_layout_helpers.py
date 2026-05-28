"""Shared layout-tree + callback-map helpers for ``tests/app``.

Why a module of helpers
-----------------------
Several tests in this directory used to ship their own copy of
``_walk`` / ``_ids`` / ``_outputs`` / ``_inputs``:

- ``tests/app/test_scene_tabs_dispatcher.py`` had ``_walk``, ``_outputs``,
  ``_inputs``, ``_callbacks_with_output``.
- ``tests/app/test_phase3_ui_layout.py`` had ``_walk``, ``_ids``,
  ``_options``.
- ``tests/app/test_view_camera_callbacks.py`` had ``_outputs``,
  ``_inputs``.
- ``tests/app/test_status_banner.py`` had ``_has_component_id``.
- ``tests/app/test_inline_edit_emits_state.py`` had
  ``_outputs_for_callback_with_input``.

The walkers all do the same thing (DFS over ``component.children``,
unwrapping callable layouts), and the callback-map introspection
helpers all want the same ``{(component_id, prop), ...}`` projection.
Duplicating them caused two real maintenance hazards:

1. The ``Dash app.layout``-as-callable workaround landed in 3 files
   independently; one of them used to forget to call it and silently
   passed against an empty layout.
2. Component children that are *strings* (Dash allows raw text
   children) made one walker explode with ``AttributeError`` while
   the others quietly skipped them.

These shared helpers normalise both quirks once.
"""
from __future__ import annotations

from typing import Iterator, Set, Tuple


# ----------------------------------------------------------------- #
# Layout-tree walkers                                                #
# ----------------------------------------------------------------- #


def walk_layout(node) -> Iterator:
    """DFS over a Dash layout tree.

    Handles three Dash quirks the per-file walkers were re-discovering:

    - ``app.layout`` may be a callable returning a fresh component
      tree on every initial-load request; unwrap it so callers don't
      iterate over the function object.
    - ``component.children`` may be ``None``, a single component, a
      list/tuple of components, or a raw string (Dash allows text
      children). Raw strings are skipped (they can't have ids).
    - The yielded sequence includes the root node so callers searching
      for a specific id don't miss the top of the tree.
    """
    if callable(node):
        node = node()
    yield node
    children = getattr(node, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if isinstance(child, str):
            continue
        yield from walk_layout(child)


def layout_ids(layout) -> Set[str]:
    """Return the set of string ``id`` attributes anywhere in a layout
    tree. Pattern-matching ids (Dash dict ids) are intentionally
    excluded -- callers that need them should walk the tree directly.
    """
    out: Set[str] = set()
    for node in walk_layout(layout):
        nid = getattr(node, "id", None)
        if isinstance(nid, str):
            out.add(nid)
    return out


def has_component_id(layout, target_id: str) -> bool:
    """Cheap recursive existence check for a string id. Equivalent to
    ``target_id in layout_ids(layout)`` but short-circuits on the
    first match -- handy for the smoke-banner / smoke-download
    presence assertions in ``test_status_banner.py``.
    """
    for node in walk_layout(layout):
        if getattr(node, "id", None) == target_id:
            return True
    return False


def find_component(layout, target_id: str):
    """Return the first component with ``id == target_id`` or
    ``None``. Callers that want to assert on the component's
    ``options`` / ``value`` / ``className`` reach for this helper.
    """
    for node in walk_layout(layout):
        if getattr(node, "id", None) == target_id:
            return node
    return None


# ----------------------------------------------------------------- #
# Callback-map introspection                                         #
# ----------------------------------------------------------------- #


def callback_outputs(callback: dict) -> Set[Tuple[str, str]]:
    """Project a ``app.callback_map[k]`` entry's Output(s) to a set of
    ``(component_id, prop)`` pairs.

    Handles both the single-output and multi-output Dash shapes
    (``cb["output"]`` is either an ``Output`` object or a list of
    them). Pattern-matching outputs (dict ids) are intentionally
    skipped -- they have no string id to match on.
    """
    out = callback.get("output")
    items = out if isinstance(out, list) else [out]
    pairs: Set[Tuple[str, str]] = set()
    for item in items:
        cid = getattr(item, "component_id", None)
        prop = getattr(item, "component_property", None)
        if isinstance(cid, str) and isinstance(prop, str):
            pairs.add((cid, prop))
    return pairs


def callback_inputs(callback: dict) -> Set[Tuple[str, str]]:
    """Project a ``app.callback_map[k]`` entry's Input(s) to a set of
    ``(component_id, prop)`` pairs. Mirrors :func:`callback_outputs`
    for the input side of the wiring.
    """
    return {
        (str(item.get("id")), item.get("property"))
        for item in callback.get("inputs", [])
    }


def callbacks_with_output(app, component_id: str, prop: str) -> list[dict]:
    """Return every callback that writes to ``(component_id, prop)``.

    The renderer layout has multiple ``allow_duplicate=True`` outputs
    pointing at the same ``(graph, figure)`` slot; this helper is the
    canonical way to enumerate them.
    """
    return [
        callback
        for callback in app.callback_map.values()
        if (component_id, prop) in callback_outputs(callback)
    ]


def outputs_for_callback_with_input(app, input_id: str) -> Set[Tuple[str, str]]:
    """Find the (single) callback that takes ``input_id`` as one of
    its triggers and project its Outputs to ``(cid, prop)`` pairs.

    This is the shape the inline-edit perf tests want
    (``test_inline_edit_emits_state.py``): given a button id,
    confirm the callback wires it through to ``agent-state-store.data``.
    Returns an empty set if no callback is found.
    """
    for cb in app.callback_map.values():
        for inp in cb.get("inputs", []):
            if inp.get("id") == input_id:
                return callback_outputs(cb)
    return set()
