"""Lock in the perf fix that makes inline polyhedra/atom-group edits
re-render on the next frame instead of waiting 5 s for the agent-state
poll.

Background
----------
On 2026-05-03 the user reported "I changed the colour, why no reaction?
And every operation has a 3-5 s delay." Root cause: ``manage_polyhedra``
and ``manage_atom_groups`` only called ``backend.patch_state(...)``
(which sets ``pending_state``); the new state then had to wait for the
``agent-state-poll`` (5 s) to round-trip through ``sync_agent_state``
before ``update_view`` re-rendered the figure.

Fix: both manage callbacks now ALSO declare ``agent-state-store.data``
as an Output (``allow_duplicate=True``), so the new state is pushed
directly to the store and ``update_view`` fires on the next frame.

This test asserts the contract: both callbacks must list
``agent-state-store.data`` among their Outputs. If you ever remove that
Output you'll silently re-introduce the 5 s latency.
"""
from __future__ import annotations

import dash

from crystal_viewer.app import create_app


def _outputs_for_callback_with_input(app: dash.Dash, input_id: str) -> set[tuple[str, str]]:
    """Find the callback that takes ``input_id`` (string id) as one of
    its triggers and return the set of (component, prop) it writes."""
    for cb in app.callback_map.values():
        for inp in cb.get("inputs", []):
            if inp.get("id") == input_id:
                outputs = cb.get("output")
                if not isinstance(outputs, list):
                    outputs = [outputs]
                seen = set()
                for o in outputs:
                    cid = getattr(o, "component_id", None)
                    cprop = getattr(o, "component_property", None)
                    if isinstance(cid, str) and isinstance(cprop, str):
                        seen.add((cid, cprop))
                return seen
    return set()


def test_manage_polyhedra_pushes_agent_state_directly():
    """Inline polyhedra edits must write ``agent-state-store.data``
    directly so ``update_view`` re-renders on the next frame instead
    of waiting for the 5 s agent-state poll."""
    app = create_app()
    outs = _outputs_for_callback_with_input(app, "polyhedra-add-btn")
    assert ("agent-state-store", "data") in outs, (
        "manage_polyhedra must Output agent-state-store.data so inline "
        "polyhedra edits (color/centre/ligand/enabled) are visible "
        "without waiting 5 s for the agent-state poll. Removing this "
        "Output reintroduces the 2026-05-03 'I changed the colour, why "
        "no reaction?' regression."
    )


def test_manage_atom_groups_pushes_agent_state_directly():
    """Inline atom-group edits must write ``agent-state-store.data``
    directly. Same rationale as polyhedra; without it every per-group
    color/visibility/opacity tweak has 0-5 s of perceived latency."""
    app = create_app()
    outs = _outputs_for_callback_with_input(app, "atom-groups-add-btn")
    assert ("agent-state-store", "data") in outs, (
        "manage_atom_groups must Output agent-state-store.data so "
        "inline atom-group edits (color/visible/opacity/material/style) "
        "are visible without waiting 5 s for the agent-state poll."
    )
