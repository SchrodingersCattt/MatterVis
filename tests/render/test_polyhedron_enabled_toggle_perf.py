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


def _backend_with_dap4(tmp_path):
    """Build a real DAP-4-backed ``ViewerBackend`` for end-to-end
    topology-cache invariance assertions.

    DAP-4 is the smallest catalog structure with a clean N + ClO4
    polyhedral structure (no SHELX disorder), so MolCrysKit's
    ``find_polyhedra`` returns deterministic shells without paying
    the SY / HPEP disorder-restoration cost. The bundle is injected
    directly so the backend doesn't have to call ``get_default_catalog``
    or read any preset.
    """
    from crystal_viewer.loader import build_loaded_crystal

    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        names=[],
    )
    bundle = build_loaded_crystal(
        name="DAP-4", cif_path="scripts/data/DAP-4.cif", title="DAP-4"
    )
    backend.bundles["DAP-4"] = bundle
    backend.structure_names = ["DAP-4"]
    backend.current_state = backend.default_state("DAP-4")
    return backend


def _state_with_dap4_specs(backend, *, clo4_enabled: bool):
    """Build a state with two polyhedron specs on DAP-4: a stable
    ``cl_o_atom`` Cl/O atom-level spec that stays enabled in both
    states, and a ``clo4_extra`` Cl/O atom-level spec whose
    ``enabled`` flag is the only difference between the two return
    shapes the tests below compare.

    Two specs (not one) are necessary so the visibility-patch test
    can confirm the *first* spec's traces stay visible while the
    *second* spec's traces flip from True to False — proving the
    patch routes by ``meta.spec_id`` and not "all polyhedra".
    """
    state = copy.deepcopy(backend.get_state())
    state["structure"] = "DAP-4"
    state["topology_enabled"] = True
    # Use a single formula unit so MCK ``find_polyhedra`` only has to
    # enumerate one ClO4 + cation worth of atoms (~14 atoms) instead
    # of the full unit cell (~336 atoms). Atom-level polyhedron
    # detection on the full cell takes >60 s on this MCK build; the
    # formula-unit path completes in <1 s and exercises the same
    # cache-key + visibility-patch contract we actually want to pin.
    state["display_mode"] = "formula_unit"
    # Pin the analysis fragment so ``resolve_topology_site`` doesn't
    # depend on species-name matching (DAP-4 fragments are formulas
    # like ``"ClO4"`` and ``"C2N2H10"``, not bare element symbols, so
    # ``species_set={"Cl"}`` would not match).
    state["topology_site_index"] = 0
    base_spec = {
        "center_species": "Cl",
        "ligand_species": "O",
        "enforce_enclosure": True,
        "level": "atom",
        "center_kind": "centroid",
    }
    state["polyhedron_specs"] = [
        {
            **base_spec,
            "id": "cl_o_atom",
            "name": "Cl/O atom",
            "color": "#ff0000",
            "enabled": True,
        },
        {
            **base_spec,
            "id": "clo4_extra",
            "name": "Cl/O extra",
            "color": "#00ff00",
            "enabled": clo4_enabled,
        },
    ]
    return state


def test_topology_context_cache_key_invariant_to_spec_enabled_on_real_bundle(tmp_path):
    """End-to-end pin (NOT just the unit-level cache-key string test):
    on a real DAP-4 bundle, ``_topology_context`` produces the same
    geometry cache key when only ``polyhedron_specs[i].enabled``
    differs.

    Without this, the topology pipeline would silently re-run MCK
    ``find_polyhedra`` on every row checkbox click even though the
    geometry is unchanged. The unit test
    ``test_figure_state_cache_key_is_invariant_to_spec_enabled_flag``
    above proves the *figure* cache key strips ``enabled``; this
    test proves the matching invariant for the *topology* cache.
    """
    backend = _backend_with_dap4(tmp_path)
    state_both = _state_with_dap4_specs(backend, clo4_enabled=True)
    state_one_off = _state_with_dap4_specs(backend, clo4_enabled=False)

    ctx_both = backend._topology_context(state_both)
    ctx_off = backend._topology_context(state_one_off)
    assert ctx_both is not None and ctx_off is not None, (
        "DAP-4 has both N and Cl species; _topology_context must "
        "resolve a site for each spec configuration."
    )
    assert ctx_both["cache_key"] == ctx_off["cache_key"], (
        "Toggling polyhedron_specs[i].enabled must NOT change the "
        "topology geometry cache key. Otherwise every row checkbox "
        "click rebuilds the entire MCK find_polyhedra payload."
    )


def _stub_topology_geometry_for(specs):
    """Build a synthetic ``topology_data`` payload that has one
    overlay per spec, so the renderer emits ``meta.spec_id``-tagged
    polyhedron traces without paying the MCK ``find_polyhedra``
    cost.

    Each overlay is marked ``is_analysis_anchor=True`` so the
    viewport-bounds filter in ``topology_background_traces`` doesn't
    drop it. The vertex set is a tiny tetrahedron near the origin --
    geometric details don't matter for the cache + visibility-patch
    contract this test pins.
    """
    spec_results = []
    for offset, spec in enumerate(specs):
        anchor_vertices = [
            [0.0 + offset, 0.0, 0.0],
            [1.0 + offset, 0.0, 0.0],
            [0.5 + offset, 1.0, 0.0],
            [0.5 + offset, 0.5, 1.0],
        ]
        spec_results.append(
            {
                "spec_id": str(spec["id"]),
                "color": str(spec["color"]),
                "fragment_label": f"spec_{offset}_anchor",
                "overlays": [
                    {
                        "shell_coords": anchor_vertices,
                        "is_analysis_anchor": True,
                        "visible": True,
                        "color": str(spec["color"]),
                        "fragment_label": f"spec_{offset}_anchor",
                    }
                ],
            }
        )
    return {"spec_results": spec_results}


def test_figure_for_state_cache_hits_and_patches_visibility_on_enabled_toggle(
    tmp_path, monkeypatch
):
    """End-to-end Phase 6 contract: two ``figure_for_state`` calls
    that differ only in ``polyhedron_specs[i].enabled`` must (a)
    land on the same ``_figure_cache`` entry and (b) emit different
    ``trace.visible`` values for traces tagged with that spec_id.

    Without this test the unit-level pieces all pass even when the
    integration is broken (cache key strips ``enabled`` but the
    visibility patch never runs, or vice versa).

    The MCK ``find_polyhedra`` call is bypassed via a stubbed
    ``topology_for_state`` so the test stays under 1 s -- the
    contract we're pinning lives in
    ``crystal_viewer.app.backend_camera`` (cache key + patch),
    not in MolCrysKit.
    """
    backend = _backend(tmp_path)  # cheap synthetic backend, no DAP-4 parse
    state_both = _state_with_specs(backend, both_enabled=True, second_enabled=True)
    state_off = _state_with_specs(backend, both_enabled=True, second_enabled=False)

    # Stub topology so build_figure receives meta-tagged traces but we
    # don't pay MCK's cost. ``topology_for_state`` is what
    # ``figure_for_state`` calls in the synchronous path
    # (``async_topology=False``), which is what we exercise here.
    specs_for_stub = state_both["polyhedron_specs"]
    stub_geometry = _stub_topology_geometry_for(specs_for_stub)
    monkeypatch.setattr(
        backend,
        "topology_for_state",
        lambda state, click_data=None: copy.deepcopy(stub_geometry),
    )

    fig_both, _ = backend.figure_for_state(state_both, async_topology=False)
    cache_size_after_first = len(backend._figure_cache)
    fig_off, _ = backend.figure_for_state(state_off, async_topology=False)
    cache_size_after_second = len(backend._figure_cache)

    assert cache_size_after_second == cache_size_after_first, (
        "Toggling polyhedron_specs[i].enabled must NOT add a new "
        "_figure_cache entry. The cache key strips enabled and the "
        "post-cache visibility patch handles the visible delta. "
        f"sizes: first={cache_size_after_first} second={cache_size_after_second}"
    )

    def _polyhedron_traces(fig):
        out: dict[str, list] = {}
        for trace in fig.data:
            meta = getattr(trace, "meta", None)
            if not isinstance(meta, dict) or meta.get("kind") != "polyhedron":
                continue
            spec_id = meta.get("spec_id")
            if not spec_id:
                continue
            out.setdefault(str(spec_id), []).append(getattr(trace, "visible", None))
        return out

    vis_both = _polyhedron_traces(fig_both)
    vis_off = _polyhedron_traces(fig_off)

    assert vis_both, (
        "Stubbed topology should produce at least one meta-tagged "
        "polyhedron trace; if not, the renderer dropped the spec_id "
        "tag somewhere on the path from topology_data -> build_figure."
    )

    spec_ids = [str(spec["id"]) for spec in specs_for_stub]
    first_id, second_id = spec_ids[0], spec_ids[1]

    # First spec stays enabled in both states -> all its traces visible.
    for vis in vis_both.get(first_id, []):
        assert bool(vis) is True
    for vis in vis_off.get(first_id, []):
        assert bool(vis) is True

    # Second spec is enabled in state_both, disabled in state_off ->
    # traces flip from True to False, NOT removed from the figure.
    assert vis_both.get(second_id), (
        f"Second spec {second_id!r} should have visible polyhedron "
        "traces in the both-enabled state."
    )
    for vis in vis_both[second_id]:
        assert bool(vis) is True
    assert vis_off.get(second_id), (
        "Disabled spec traces must remain in the figure (just with "
        "visible=False) -- the cache hit must NOT skip them or the "
        "next toggle back to enabled would re-pay the MCK cost."
    )
    for vis in vis_off[second_id]:
        assert vis is False, (
            "Disabled polyhedron spec traces must be hidden via the "
            "post-cache visibility patch (visible=False), not absent."
        )


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
