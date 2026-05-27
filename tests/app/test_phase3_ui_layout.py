"""Phase 3 UI: left-panel Polyhedra and Atom-Groups tables.

These tests pin the *layout* contract:

  * The new section ids exist in the rendered Dash layout. External
    automation (and the agent transcripts, see ``agents/``) scrape
    these ids; renaming them is a back-incompatible change.
  * The Phase 3 row builders return Dash ``html.Div`` containers
    whose nested input ids use the pattern-matching shape
    ``{"type": "...", "spec_id"|"group_id": "..."}`` so the manage
    callback's ALL inputs survive a rename of any single row.
  * The legacy ``Monochrome atoms`` checkbox is GONE from the
    display-options checklist; users add a Monochrome atom-group
    via the Phase 3 preset button instead.

DO NOT REMOVE -- this guards the contract documented in
``agents/atom_groups_api.md`` and ``agents/polyhedron_api.md``.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Iterable

import pytest

from crystal_viewer.app import (
    WORKSPACE_DIR,
    _AUTO_LIGAND_VALUE,
    _atom_groups_table_rows,
    _polyhedra_table_rows,
    create_app,
)


def _walk(node):
    # Dash supports ``app.layout`` being a callable (returns a fresh
    # Component on each initial-load request). Unwrap once so the rest
    # of these tests keep walking the component tree they expect.
    if callable(node):
        node = node()
    yield node
    for child in (getattr(node, "children", None) or []):
        if isinstance(child, str):
            continue
        yield from _walk(child)


def _ids(layout) -> set:
    out = set()
    for node in _walk(layout):
        nid = getattr(node, "id", None)
        if isinstance(nid, str):
            out.add(nid)
    return out


def _options(layout, target_id: str) -> list[dict]:
    for node in _walk(layout):
        if getattr(node, "id", None) == target_id:
            return list(getattr(node, "options", None) or [])
    return []


@pytest.fixture
def app(tmp_path: Path):
    return create_app(preset_path=str(tmp_path / "preset.json"), root_dir=WORKSPACE_DIR)


def test_phase3_panel_sections_present(app):
    ids = _ids(app.layout)
    expected = {
        "polyhedra-add-btn",
        "polyhedra-rows-container",
        "atom-groups-add-btn",
        "atom-groups-rows-container",
        "atom-groups-preset-mono",
        "atom-groups-clear-btn",
    }
    missing = expected - ids
    assert not missing, f"missing Phase 3 UI ids: {missing}"


def test_legacy_topology_species_checklist_removed(app):
    """The legacy ``topology-species`` checklist + ``topology-hull-color``
    picker have been replaced by the Named-polyhedra table. Their ids
    must NOT be present in the layout; otherwise Dash will silently
    accept user clicks on dead controls.
    """
    ids = _ids(app.layout)
    legacy = {"topology-species", "topology-hull-color"}
    overlap = legacy & ids
    assert not overlap, (
        f"legacy topology UI ids must be removed (use polyhedra "
        f"table instead): {overlap}"
    )


def test_hide_h_preset_removed(app):
    """The ``Hide H`` atom-groups preset duplicated the existing
    ``Hydrogens`` checkbox under Display options. Removed in favour of
    the single canonical control; this test pins that decision.
    """
    ids = _ids(app.layout)
    assert "atom-groups-preset-hide-h" not in ids, (
        "Hide H atom-group preset is intentionally gone -- use the "
        "Hydrogens checkbox in Display options."
    )


def test_legacy_monochrome_checkbox_removed_from_display_options(app):
    options = _options(app.layout, "display-options")
    values = [opt.get("value") for opt in options]
    assert "monochrome" not in values, (
        "Monochrome atoms checkbox should be removed from the display "
        "options checklist; the Phase 3 atom-groups preset button is "
        "the new entry point."
    )


def test_polyhedra_table_rows_render_pattern_matched_inputs():
    spec = {
        "id": "abcd",
        "name": "test",
        "color": "#ff0000",
        "center_species": "ClO4",
        "ligand_species": None,
        "enabled": True,
    }
    rows = _polyhedra_table_rows([spec], [{"label": "ClO4 ×4", "value": "ClO4"}])
    type_ids = {
        getattr(child, "id", {}).get("type")
        for row in rows
        for node in _walk(row)
        for child in (getattr(node, "children", None) or [])
        if isinstance(getattr(child, "id", None), dict)
    }
    expected_types = {
        "poly-row-color",
        "poly-row-center",
        "poly-row-ligand",
        "poly-row-enabled",
        "poly-row-delete",
    }
    assert expected_types <= type_ids, (
        f"missing per-row pattern-match types: {expected_types - type_ids}"
    )


def test_polyhedra_table_rows_empty_state_has_helper_message():
    rows = _polyhedra_table_rows([], [])
    children = list(rows)
    assert len(children) == 1
    text = str(getattr(children[0], "children", ""))
    assert "Add" in text


def test_atom_groups_table_rows_render_pattern_matched_inputs():
    group = {
        "id": "grp_z",
        "name": "all-grey",
        "selector": {"all": True},
        "color": "#888888",
        "color_light": None,
        "visible": True,
        "opacity": None,
        "material": None,
        "style": None,
    }
    rows = _atom_groups_table_rows([group], [{"label": "O", "value": "O"}])
    type_ids = set()
    for row in rows:
        for node in _walk(row):
            nid = getattr(node, "id", None)
            if isinstance(nid, dict) and "type" in nid:
                type_ids.add(nid["type"])
    expected_types = {
        "ag-row-visible",
        "ag-row-color",
        "ag-row-kind",
        "ag-row-elements",
        "ag-row-opacity",
        "ag-row-delete",
    }
    assert expected_types <= type_ids, (
        f"missing per-row pattern-match types: {expected_types - type_ids}"
    )


def test_polyhedra_ligand_dropdown_offers_auto():
    spec = {
        "id": "x",
        "name": "x",
        "color": "#ff0000",
        "center_species": "ClO4",
        "ligand_species": None,
        "enabled": True,
    }
    rows = _polyhedra_table_rows([spec], [{"label": "ClO4 ×4", "value": "ClO4"}])
    auto_seen = False
    for row in rows:
        for node in _walk(row):
            options = getattr(node, "options", None)
            if not options:
                continue
            for opt in options:
                if isinstance(opt, dict) and opt.get("value") == _AUTO_LIGAND_VALUE:
                    auto_seen = True
                    break
    assert auto_seen, "ligand dropdown must offer an (auto) option"
