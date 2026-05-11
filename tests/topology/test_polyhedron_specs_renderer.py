"""Phase 1 polyhedron-specs -- renderer/topology integration.

Two invariants worth pinning down:

1. ``ViewerBackend.topology_for_state`` produces the new
   ``spec_results`` shape when ``polyhedron_specs`` is set, with one
   entry per enabled spec, each carrying its own ``color`` and a list
   of ``overlays`` (the analysis anchor flagged with
   ``is_analysis_anchor=True``).

2. ``crystal_viewer.renderer.topology_background_traces`` and
   ``topology_foreground_traces`` paint each spec with its own colour,
   not a single shared ``style["topology_hull_color"]``. The legacy
   single-colour path keeps working when ``spec_results`` is absent
   (back-compat for callers that synthesise topology dicts by hand).

DO NOT REMOVE -- the multi-colour rendering is the user-visible
deliverable for the polyhedron-specs Phase 1 work.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import WORKSPACE_DIR, ViewerBackend
from crystal_viewer.presets import default_preset_path
from crystal_viewer.renderer import topology_background_traces, topology_foreground_traces


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    # Use the repo workspace as ``root_dir`` so the default catalogue
    # (DAP-4 etc.) actually gets loaded; the autouse ``_isolated_scene_store``
    # fixture in ``tests/conftest.py`` still redirects scene-store writes to
    # ``tmp_path`` so this stays side-effect-free.
    return ViewerBackend(preset_path=default_preset_path(), root_dir=WORKSPACE_DIR)


# ---- topology_for_state shape --------------------------------------------


def test_topology_for_state_emits_spec_results_per_enabled_spec(backend: ViewerBackend):
    # Default state ships with topology_species_keys = ["N", "C6N2"];
    # add named specs that match those formulas so the analysis anchor
    # resolves on either species depending on the click.
    spec_a = backend.add_polyhedron_spec(
        center_species="N", color="#FF0000", name="ammonium"
    )
    spec_b = backend.add_polyhedron_spec(
        center_species="C6N2", color="#0000FF", name="DABCO ring"
    )
    state = backend.get_state()

    topology = backend.topology_for_state(state)
    assert topology is not None, "DAP-4 must yield a non-empty topology"

    spec_results = topology.get("spec_results")
    assert isinstance(spec_results, list)
    assert {entry["spec_id"] for entry in spec_results} == {spec_a["id"], spec_b["id"]}

    by_id = {entry["spec_id"]: entry for entry in spec_results}
    assert by_id[spec_a["id"]]["color"] == "#ff0000"
    assert by_id[spec_b["id"]]["color"] == "#0000ff"

    # Every enabled spec produced at least one overlay (otherwise the
    # painter has nothing to draw and the test would silently pass even
    # if the multi-spec wiring was broken).
    for entry in spec_results:
        assert entry["overlays"], (
            f"spec {entry['spec_id']!r} produced no overlays; "
            "_compute_topology_geometry probably skipped it"
        )

    # Exactly one overlay across all specs is the analysis anchor.
    anchor_count = sum(
        1
        for entry in spec_results
        for overlay in entry["overlays"]
        if overlay.get("is_analysis_anchor")
    )
    assert anchor_count == 1, (
        f"expected exactly one analysis anchor, got {anchor_count}; "
        "renderer would render two anchor markers and confuse the user"
    )
    assert topology.get("analysis_spec_id") in {spec_a["id"], spec_b["id"]}


def test_topology_for_state_caches_geometry_across_color_changes(backend: ViewerBackend):
    spec = backend.add_polyhedron_spec(center_species="N", color="#FF0000")
    state = backend.get_state()
    first = backend.topology_for_state(state)
    assert first is not None
    cache = backend.get_bundle(state["structure"])._topology_state_cache
    cached_keys_first = set(cache.keys())

    # Mutating only the colour must not blow away the geometry cache --
    # the renderer's painter cache is keyed on colour separately, but
    # the heavy coordination-shell extraction stays a single entry.
    backend.update_polyhedron_spec(spec["id"], {"color": "#00FF00"})
    state = backend.get_state()
    second = backend.topology_for_state(state)
    assert second is not None
    cached_keys_second = set(cache.keys())
    assert cached_keys_first == cached_keys_second, (
        "_topology_state_cache should not grow when only colours change"
    )

    by_color = {entry["spec_id"]: entry["color"] for entry in second["spec_results"]}
    assert by_color[spec["id"]] == "#00ff00", "new colour must reach spec_results"


def test_topology_for_state_falls_back_to_legacy_species_keys(backend: ViewerBackend):
    # No explicit polyhedron_specs --> legacy ``topology_species_keys``
    # must still drive a topology that the renderer can paint.
    state = backend.get_state()
    state["polyhedron_specs"] = []
    state["topology_species_keys"] = ["N"]
    state["topology_hull_color"] = "#abcdef"
    topology = backend.topology_for_state(state)
    assert topology is not None
    spec_results = topology.get("spec_results")
    assert spec_results, "legacy path must still synthesise spec_results"
    assert all(entry["color"].lower() == "#abcdef" for entry in spec_results)


# ---- renderer painter ----------------------------------------------------


def _hex(value):
    return value.lower() if isinstance(value, str) else value


def test_topology_background_traces_paints_distinct_colors_per_spec():
    topology_data = {
        "spec_results": [
            {
                "spec_id": "a",
                "color": "#ff0000",
                "overlays": [
                    {
                        "shell_coords": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        "is_analysis_anchor": True,
                        "center_coords": [0.25, 0.25, 0.25],
                    }
                ],
            },
            {
                "spec_id": "b",
                "color": "#00ff00",
                "overlays": [
                    {
                        "shell_coords": [[5, 5, 5], [6, 5, 5], [5, 6, 5], [5, 5, 6]],
                        "is_analysis_anchor": False,
                        "center_coords": [5.25, 5.25, 5.25],
                    }
                ],
            },
        ],
    }
    traces = topology_background_traces(topology_data, style={"topology_hull_color": "#7c5cbf"})

    colors = {_hex(tr.get("color")) for tr in traces if tr.get("color")}
    line_colors = {
        _hex((tr.get("line") or {}).get("color"))
        for tr in traces
        if isinstance(tr.get("line"), dict)
    }
    rendered = colors | line_colors
    rendered.discard(None)
    assert "#ff0000" in rendered
    assert "#00ff00" in rendered
    assert "#7c5cbf" not in rendered, (
        "fallback hull colour must not leak when spec_results carries explicit colours"
    )


def test_topology_background_cache_is_keyed_on_color_tuple():
    topology_data = {
        "spec_results": [
            {
                "spec_id": "a",
                "color": "#ff0000",
                "overlays": [
                    {
                        "shell_coords": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        "is_analysis_anchor": True,
                    }
                ],
            }
        ],
    }
    first = topology_background_traces(topology_data)
    cache = topology_data["_background_dict_cache"]
    assert len(cache) == 1
    # Same call -> cache hit, no new entry.
    second = topology_background_traces(topology_data)
    assert second is first
    assert len(cache) == 1
    # Mutating the colour and recalling must produce a NEW cache entry
    # without invalidating the old one (separate painter permutations
    # for adjacent colour swaps).
    topology_data["spec_results"][0]["color"] = "#00ff00"
    third = topology_background_traces(topology_data)
    assert len(cache) == 2
    third_colors = {_hex(tr.get("color")) for tr in third if tr.get("color")}
    third_colors |= {
        _hex((tr.get("line") or {}).get("color"))
        for tr in third
        if isinstance(tr.get("line"), dict)
    }
    assert "#00ff00" in third_colors


def test_topology_background_traces_legacy_path_still_works():
    # No spec_results -> falls back to single-colour painter driven by
    # style["topology_hull_color"]. Ensures pre-Phase-1 callers and
    # hand-built test fixtures keep working.
    topology_data = {
        "shell_coords": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "extra_overlays": [
            {"shell_coords": [[5, 5, 5], [6, 5, 5], [5, 6, 5], [5, 5, 6]]}
        ],
    }
    traces = topology_background_traces(topology_data, style={"topology_hull_color": "#abcdef"})
    rendered = {_hex(tr.get("color")) for tr in traces if tr.get("color")}
    rendered |= {
        _hex((tr.get("line") or {}).get("color"))
        for tr in traces
        if isinstance(tr.get("line"), dict)
    }
    assert "#abcdef" in rendered


def test_topology_foreground_paints_anchor_distance_with_anchor_spec_color():
    topology_data = {
        "center_coords": [0.0, 0.0, 0.0],
        "shell_coords": [[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0]],
        "distances": [1.0, 1.0, 1.0, 1.0],
        "analysis_spec_id": "anchor",
        "spec_results": [
            {
                "spec_id": "anchor",
                "color": "#ff0000",
                "overlays": [
                    {
                        "shell_coords": [[1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 0]],
                        "center_coords": [0.0, 0.0, 0.0],
                        "is_analysis_anchor": True,
                    }
                ],
            },
            {
                "spec_id": "side",
                "color": "#00ff00",
                "overlays": [
                    {
                        "shell_coords": [[5, 5, 5], [6, 5, 5], [5, 6, 5], [5, 5, 6]],
                        "center_coords": [5.5, 5.5, 5.5],
                        "is_analysis_anchor": False,
                    }
                ],
            },
        ],
    }
    traces = topology_foreground_traces(topology_data, style={"topology_hull_color": "#7c5cbf"})
    rendered = set()
    for tr in traces:
        for key in ("color",):
            value = tr.get(key)
            if value:
                rendered.add(_hex(value))
        line = tr.get("line")
        if isinstance(line, dict) and line.get("color"):
            rendered.add(_hex(line["color"]))
        marker = tr.get("marker")
        if isinstance(marker, dict) and marker.get("color"):
            rendered.add(_hex(marker["color"]))
    # Anchor distance markers must use the anchor spec's colour, and the
    # extra-spec markers must be present too (so the user can see at a
    # glance where the second-coloured polyhedra are).
    assert "#ff0000" in rendered
    assert "#00ff00" in rendered
