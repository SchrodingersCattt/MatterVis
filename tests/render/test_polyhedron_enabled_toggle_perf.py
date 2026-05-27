"""Phase 6 instant-toggle invariant: spec-level ``enabled`` is patched
onto ``trace.visible`` *post-cache*, so toggling a row checkbox does
NOT bust ``_figure_cache``.

Before this change every checkbox click filtered the spec out of
``_effective_polyhedron_specs`` upstream, which baked the absence
into the figure body and produced a different cache key per
toggle. On the user's DAP-4321 / SY scenes that meant every click
paid the full 200-400 ms ``build_figure`` cost. The user reported
the lag as "为什么不是迅速刷新".

Now:
- ``_effective_polyhedron_specs`` returns *all* specs (enabled or
  not) so the topology pipeline stays cache-hit-friendly.
- ``_figure_state_cache_key`` strips the ``enabled`` field so two
  states differing only in a checkbox land on the same key.
- Every polyhedron overlay trace carries ``meta.spec_id``, and
  ``figure_for_state`` walks ``fig.data`` after each cache lookup
  to flip ``visible`` per the live spec's ``enabled`` flag.
"""

from __future__ import annotations

import copy

from crystal_viewer.app import ViewerBackend


def _backend(tmp_path):
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
    )


def _state_with_specs(backend, *, both_enabled: bool, second_enabled: bool):
    state = copy.deepcopy(backend.get_state())
    state["topology_enabled"] = True
    state["polyhedron_specs"] = [
        {
            "id": "p1",
            "name": "spec-1",
            "center_species": "Pb",
            "ligand_species": "I",
            "color": "#ff0000",
            "enabled": both_enabled,
            "enforce_enclosure": True,
            "level": "atom",
            "center_kind": "centroid",
        },
        {
            "id": "p2",
            "name": "spec-2",
            "center_species": "Pb",
            "ligand_species": "I",
            "color": "#00ff00",
            "enabled": second_enabled,
            "enforce_enclosure": True,
            "level": "atom",
            "center_kind": "centroid",
        },
    ]
    return state


def test_figure_state_cache_key_is_invariant_to_spec_enabled_flag(tmp_path):
    backend = _backend(tmp_path)
    state_both = _state_with_specs(backend, both_enabled=True, second_enabled=True)
    state_one_off = _state_with_specs(backend, both_enabled=True, second_enabled=False)

    # The figure body is identical (same cache entry), but the
    # post-cache patch will flip visibility on p2's traces.
    key_both = backend._figure_state_cache_key(state_both)
    key_off = backend._figure_state_cache_key(state_one_off)
    assert key_both == key_off, (
        "Toggling polyhedron_specs[i].enabled must NOT change the "
        "figure cache key -- otherwise every row checkbox click pays "
        "the full build_figure cost."
    )


def test_figure_state_cache_key_still_distinguishes_geometry_changes(tmp_path):
    backend = _backend(tmp_path)
    state_a = _state_with_specs(backend, both_enabled=True, second_enabled=True)
    state_b = copy.deepcopy(state_a)
    # Change something that *does* affect geometry: a different
    # ligand species. Cache key MUST differ so the renderer rebuilds.
    state_b["polyhedron_specs"][0]["ligand_species"] = "Cl"

    assert backend._figure_state_cache_key(state_a) != backend._figure_state_cache_key(state_b)


def test_polyhedron_traces_carry_meta_spec_id(tmp_path):
    """Architecture pin: every polyhedron overlay must be tagged with
    ``meta={"spec_id": ..., "kind": "polyhedron"}`` so
    ``_apply_polyhedron_visibility_patch`` can route the live
    enabled flag onto the right traces.
    """
    from crystal_viewer.renderer import topology_background_traces, topology_foreground_traces

    topology_data = {
        "spec_results": [
            {
                "spec_id": "spec-A",
                "name": "Pb-I",
                "color": "#ff0000",
                "overlays": [
                    {
                        "center_label": "P1",
                        "shell_coords": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        "color": "#ff0000",
                        "visible": True,
                        "is_analysis_anchor": True,
                        "center_coords": [0.25, 0.25, 0.25],
                    }
                ],
            }
        ],
        "center_coords": [0.25, 0.25, 0.25],
        "shell_coords": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "distances": [1.0, 1.0, 1.0, 1.0],
        "analysis_spec_id": "spec-A",
    }
    bg = topology_background_traces(topology_data, style={})
    fg = topology_foreground_traces(topology_data, style={})

    # Every polyhedron-y trace returned must carry the spec-A tag.
    for trace in bg + fg:
        meta = trace.get("meta") if isinstance(trace, dict) else getattr(trace, "meta", None)
        assert meta is not None, f"polyhedron trace missing meta: {trace}"
        assert isinstance(meta, dict)
        assert meta.get("kind") == "polyhedron"
        assert meta.get("spec_id") == "spec-A"


def test_apply_visibility_patch_flips_disabled_spec_traces(tmp_path):
    """Walks the actual fig.data and confirms ``visible`` is flipped
    on the disabled spec's traces while the enabled spec stays on.
    """
    import plotly.graph_objects as go

    from crystal_viewer.app.backend_camera import _apply_polyhedron_visibility_patch

    fig = go.Figure(
        data=[
            go.Scatter3d(x=[0], y=[0], z=[0], meta={"spec_id": "p1", "kind": "polyhedron"}),
            go.Scatter3d(x=[0], y=[0], z=[0], meta={"spec_id": "p2", "kind": "polyhedron"}),
            go.Scatter3d(x=[0], y=[0], z=[0]),  # untagged: must stay visible
        ]
    )
    state = {
        "polyhedron_specs": [
            {"id": "p1", "enabled": True},
            {"id": "p2", "enabled": False},
        ]
    }
    _apply_polyhedron_visibility_patch(fig, state)
    assert fig.data[0].visible is True
    assert fig.data[1].visible is False
    # Untagged traces must not be touched.
    assert fig.data[2].visible is None or fig.data[2].visible is True


def test_effective_polyhedron_specs_returns_all_specs(tmp_path):
    """Counterpart invariant on the topology side: ``_effective_-
    polyhedron_specs`` must include disabled specs so the topology
    cache key is invariant to the enabled flag, mirroring the
    figure cache trick.
    """
    backend = _backend(tmp_path)
    state_both = _state_with_specs(backend, both_enabled=True, second_enabled=True)
    state_one_off = _state_with_specs(backend, both_enabled=True, second_enabled=False)
    specs_both = backend._effective_polyhedron_specs(state_both)
    specs_off = backend._effective_polyhedron_specs(state_one_off)
    assert len(specs_both) == len(specs_off) == 2, (
        "_effective_polyhedron_specs must return ALL specs (enabled "
        "or not) so the topology cache key stays invariant to the "
        "row checkbox -- otherwise the topology pipeline reruns "
        "on every click."
    )
    # The enabled flag still rides on each spec for downstream
    # consumers (REST, UI, post-cache patch).
    assert [s.get("enabled") for s in specs_off] == [True, False]
