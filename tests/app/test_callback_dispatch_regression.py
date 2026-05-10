"""Lock in the Dash 2.18 callback-graph rule that the UI relies on.

Background
----------
On 2026-05-03 we shipped a tabbed scenes UI. Two callbacks --
``load_scene_tab`` (triggered by ``scene-tabs.value``) and
``sync_agent_state`` (triggered by ``agent-state-poll.n_intervals``) --
both wrote the same set of control props (display-options, atom-scale,
material-selector, ...). ``load_scene_tab`` declared its Outputs with
``allow_duplicate=True`` while ``sync_agent_state`` did not. Each one of
those props is also an ``Input`` of ``capture_state`` (so user clicks
should fire callbacks).

That mix turned out to silently disable Dash's user-event listener on
those props: clicking Labels still toggled the DOM, but no
``/_dash-update-component`` POST was ever sent, the figure froze, and
the entire left panel looked dead. There was no Python traceback and
no JS console error -- the only signal was that ``capture_state``
never appeared in the callback log.

The fix was to merge the two callbacks into one writer per prop. This
test guards that invariant by asserting that *no control-shaped prop
participates in the (Input ∩ Output(allow_dup) ∩ Output(non-allow_dup))
combination* that triggered the bug.
"""
from __future__ import annotations

import dash

from crystal_viewer.app import create_app


def _all_callbacks(app: dash.Dash):
    """Return the dict of callback specs keyed by output id, mirroring
    what Dash exposes at ``/_dash-dependencies`` (we use the in-process
    map to avoid spinning a real server)."""
    return list(app.callback_map.values())


def _input_props(callbacks):
    inputs: set[tuple[str, str]] = set()
    for cb in callbacks:
        for inp in cb.get("inputs", []):
            cid = inp.get("id")
            cprop = inp.get("property")
            if isinstance(cid, str) and isinstance(cprop, str):
                inputs.add((cid, cprop))
    return inputs


def _output_writers(callbacks):
    """Return ``{(component, prop): {"primary": int, "duplicate": int}}``."""
    writers: dict[tuple[str, str], dict[str, int]] = {}
    for cb in callbacks:
        out = cb.get("output")
        items = out if isinstance(out, list) else [out]
        for o in items:
            if o is None:
                continue
            cid = getattr(o, "component_id", None)
            cprop = getattr(o, "component_property", None)
            if not isinstance(cid, str) or not isinstance(cprop, str):
                # Pattern-matching ids (dicts) are not part of the bug surface.
                continue
            bucket = writers.setdefault((cid, cprop), {"primary": 0, "duplicate": 0})
            if getattr(o, "allow_duplicate", False):
                bucket["duplicate"] += 1
            else:
                bucket["primary"] += 1
    return writers


# Props that we have verified by hand still dispatch correctly even
# though they technically match the "primary + duplicate writer + Input"
# shape. Empirically the bug only triggers under a narrower (still
# poorly understood) pattern; this allowlist keeps the lint actionable
# without forcing every benign coincidence to become an error.
KNOWN_WORKING_MIXED_PROPS: set[tuple[str, str]] = {
    ("scene-tabs", "children"),
    ("scene-tabs", "value"),
    ("topology-site-index", "value"),
    ("status-dismiss-timer", "n_intervals"),
    ("agent-state-store", "data"),
}

# Props on user-facing controls (Checklist/Slider/Dropdown/Input/etc.)
# whose user-event dispatch we *especially* care about: these were the
# ones that became dead in the 2026-05-03 regression.
SENSITIVE_USER_INPUT_PROPS: set[tuple[str, str]] = {
    ("display-mode-selector", "value"),
    ("display-options", "value"),
    ("atom-scale-slider", "value"),
    ("bond-radius-slider", "value"),
    ("minor-opacity-slider", "value"),
    ("material-selector", "value"),
    ("style-selector", "value"),
    ("disorder-selector", "value"),
    ("axis-scale-slider", "value"),
    ("topology-toggle", "value"),
}


def test_no_new_control_prop_mixes_primary_and_duplicate_writers():
    """Each control prop must have exactly one writer pattern. Mixing a
    primary (non-allow_duplicate) writer with one or more duplicate
    (``allow_duplicate=True``) writers can silently disable Dash 2.18's
    user-event listener on the prop -- the DOM still toggles but no
    ``/_dash-update-component`` POST ever fires.

    See file docstring for the original incident."""
    app = create_app()
    callbacks = _all_callbacks(app)
    inputs = _input_props(callbacks)
    writers = _output_writers(callbacks)

    offenders = []
    for prop, counts in writers.items():
        if prop not in inputs:
            continue
        if counts["primary"] < 1 or counts["duplicate"] < 1:
            continue
        if prop in KNOWN_WORKING_MIXED_PROPS:
            continue
        offenders.append((prop, counts))

    assert not offenders, (
        "These component props became 'mixed writers' (one primary and "
        "one or more allow_duplicate writers) AND are read as an Input. "
        "In Dash 2.18 that silently disables the user-event dispatch on "
        "the prop -- buttons/sliders/dropdowns still update the DOM but "
        "no callback ever fires. Either merge the writers into a single "
        "callback or make every writer use the same allow_duplicate "
        "setting. If you have *manually* verified the prop still works "
        "in a real browser, add it to KNOWN_WORKING_MIXED_PROPS.\n"
        f"Offending props: {offenders}"
    )


def test_sensitive_user_input_props_have_a_single_writer():
    """The 2026-05-03 regression killed Labels/Display Scope/Material/
    Style/Disorder/etc. on real user clicks. Those control props must
    therefore have exactly one writer pattern -- a single primary writer
    OR only duplicate writers, never the mix that triggered the bug."""
    app = create_app()
    callbacks = _all_callbacks(app)
    writers = _output_writers(callbacks)

    offenders = []
    for prop in SENSITIVE_USER_INPUT_PROPS:
        counts = writers.get(prop)
        if counts is None:
            continue
        if counts["primary"] >= 1 and counts["duplicate"] >= 1:
            offenders.append((prop, counts))

    assert not offenders, (
        "User-facing control props must NEVER mix primary and duplicate "
        "writers -- that pattern triggered the 2026-05-03 dead-UI "
        "regression where clicks toggled the DOM but no Dash callback "
        "ever fired. Merge the conflicting callbacks into one (see "
        "``sync_agent_state`` for an example).\n"
        f"Offending props: {offenders}"
    )
