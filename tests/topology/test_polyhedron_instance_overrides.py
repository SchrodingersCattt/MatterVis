"""Phase 4: per-fragment ``instance_overrides`` on polyhedron specs.

A spec carries ``{fragment_label: {color, visible}}`` overrides so the
right-click "set this one cyan" path doesn't fight the spec-level
default colour. The renderer's ``_attach_spec_colors`` helper is the
single source of truth for applying these on top of the shared
geometry payload, and the backend's CRUD helpers wrap the persistence.

DO NOT REMOVE -- this guards the contract documented in
``agents/polyhedron_api.md``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import (
    ViewerBackend,
    _coerce_instance_overrides,
    _normalize_polyhedron_spec,
)
from crystal_viewer.presets import default_preset_path


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))


# ---- normaliser unit tests ----------------------------------------------


def test_normalize_polyhedron_spec_includes_empty_instance_overrides():
    spec = _normalize_polyhedron_spec(
        {"center_species": "Cl"},
        fallback_color="#7C5CBF",
        existing_ids=set(),
    )
    assert spec is not None
    assert spec["instance_overrides"] == {}


def test_normalize_polyhedron_spec_keeps_dict_instance_overrides():
    spec = _normalize_polyhedron_spec(
        {
            "center_species": "Cl",
            "instance_overrides": {
                "B0": {"color": "#FF0000"},
                "B1": {"visible": False},
            },
        },
        fallback_color="#7C5CBF",
        existing_ids=set(),
    )
    assert spec is not None
    assert spec["instance_overrides"]["B0"] == {"color": "#ff0000"}
    assert spec["instance_overrides"]["B1"] == {"visible": False}


def test_coerce_instance_overrides_accepts_list_form():
    out = _coerce_instance_overrides(
        [
            {"label": "B0", "color": "#00FF00"},
            {"label": "B1", "visible": True},
        ]
    )
    assert out["B0"] == {"color": "#00ff00"}
    assert out["B1"] == {"visible": True}


def test_coerce_instance_overrides_drops_invalid_entries():
    out = _coerce_instance_overrides(
        {
            "B0": {"color": "not-a-hex"},
            "B1": {},                # empty -> dropped
            "B2": {"color": "#ABCDEF", "visible": False},
        }
    )
    # B0's invalid colour is dropped; with no other field, the entry has
    # nothing to record so it falls out of the map.
    assert "B0" not in out
    assert "B1" not in out
    assert out["B2"] == {"color": "#abcdef", "visible": False}


# ---- backend CRUD -------------------------------------------------------


def test_set_polyhedron_instance_override_round_trips(backend: ViewerBackend):
    structure = backend.structure_names[0]
    backend.patch_state({"structure": structure})
    spec = backend.add_polyhedron_spec(center_species="Cl", color="#7C5CBF")
    spec = backend.set_polyhedron_instance_override(
        spec_id=spec["id"],
        fragment_label="X3",
        override={"color": "#22DD22", "visible": True},
    )
    assert spec["instance_overrides"]["X3"] == {"color": "#22dd22", "visible": True}

    # Round-trip through state to confirm persistence.
    persisted = next(s for s in backend.list_polyhedron_specs() if s["id"] == spec["id"])
    assert persisted["instance_overrides"]["X3"] == {"color": "#22dd22", "visible": True}


def test_clear_polyhedron_instance_override_removes_entry(backend: ViewerBackend):
    structure = backend.structure_names[0]
    backend.patch_state({"structure": structure})
    spec = backend.add_polyhedron_spec(center_species="Cl", color="#7C5CBF")
    backend.set_polyhedron_instance_override(
        spec_id=spec["id"], fragment_label="X3", override={"color": "#22DD22"}
    )
    cleared = backend.clear_polyhedron_instance_override(
        spec_id=spec["id"], fragment_label="X3"
    )
    assert "X3" not in cleared["instance_overrides"]


def test_unknown_spec_id_raises(backend: ViewerBackend):
    with pytest.raises(KeyError):
        backend.set_polyhedron_instance_override(
            spec_id="nope", fragment_label="X0", override={"color": "#000000"}
        )


# ---- renderer overlay layer --------------------------------------------
#
# The instance_overrides flow through ``_attach_spec_colors`` onto each
# overlay's ``color`` / ``visible`` fields; ``topology_background_traces``
# then groups overlays by colour, so two distinct colours produce two
# merged-mesh traces and a hidden overlay produces no trace at all.


def test_attach_spec_colors_applies_overrides(backend: ViewerBackend):
    cached_geometry = {
        "spec_results": [
            {
                "spec_id": "p1",
                "name": "Cl",
                "overlays": [
                    {"center_label": "X0", "shell_coords": [[0, 0, 0]]},
                    {"center_label": "X1", "shell_coords": [[1, 0, 0]]},
                ],
            }
        ]
    }
    effective_specs = [
        {
            "id": "p1",
            "name": "Cl",
            "color": "#7C5CBF",
            "instance_overrides": {
                "X0": {"color": "#FF0000"},
                "X1": {"visible": False},
            },
        }
    ]
    out = backend._attach_spec_colors(cached_geometry, effective_specs)
    overlays = out["spec_results"][0]["overlays"]
    assert overlays[0]["color"] == "#FF0000"
    assert overlays[1]["visible"] is False


def test_renderer_renders_overrides_as_separate_colour_buckets():
    """``topology_background_traces`` buckets overlays by colour. Two
    different per-fragment colours must produce two trace groups
    (one per colour); a hidden fragment must produce zero."""
    from crystal_viewer.renderer import topology_background_traces

    topology_data = {
        "spec_results": [
            {
                "spec_id": "p1",
                "name": "Cl",
                "color": "#7C5CBF",
                "overlays": [
                    {
                        "center_label": "X0",
                        "shell_coords": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                        "color": "#FF0000",
                        "visible": True,
                        "is_analysis_anchor": False,
                    },
                    {
                        "center_label": "X1",
                        "shell_coords": [[2, 0, 0], [3, 0, 0], [2, 1, 0], [2, 0, 1]],
                        "color": "#00FF00",
                        "visible": True,
                        "is_analysis_anchor": False,
                    },
                    {
                        "center_label": "X2",
                        "shell_coords": [[4, 0, 0], [5, 0, 0], [4, 1, 0], [4, 0, 1]],
                        "visible": False,
                        "is_analysis_anchor": False,
                    },
                ],
            }
        ]
    }
    traces = topology_background_traces(topology_data, style={})
    colours = {tr.get("color") for tr in traces if tr.get("color")}
    assert "#FF0000" in colours
    assert "#00FF00" in colours
    # The hidden overlay's spec colour does NOT appear -- nothing else
    # in the trace list should have produced it.
    assert "#7C5CBF" not in colours
