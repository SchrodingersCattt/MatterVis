"""Phase 2 atom_groups data model -- backend layer.

These tests pin down:

  * ``_normalize_atom_group(s)`` coerces user payloads into the
    canonical shape and rejects unsalvageable rows.
  * ``ViewerBackend.add_/update_/remove_/reorder_atom_group(s)``
    operate on the active scene's state, persist via ``patch_state``,
    and survive a round-trip through the scene store.
  * ``style_for_state`` exports ``atom_groups`` so the renderer
    dispatcher can pick up overrides.

DO NOT REMOVE -- this guards the contract documented in
``agents/atom_groups_api.md``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from crystal_viewer.app import (
    ViewerBackend,
    _normalize_atom_group,
    _normalize_atom_groups,
)
from crystal_viewer.presets import default_preset_path


@pytest.fixture
def backend(tmp_path: Path) -> ViewerBackend:
    return ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))


# ---- normaliser unit tests ------------------------------------------------


def test_normalize_atom_group_accepts_all_selector():
    existing: set[str] = set()
    group = _normalize_atom_group(
        {"selector": {"all": True}, "color": "#FF0000"},
        existing_ids=existing,
    )
    assert group is not None
    assert group["selector"] == {"all": True}
    assert group["color"] == "#ff0000"
    assert group["visible"] is True
    assert group["id"] in existing


def test_normalize_atom_group_accepts_elements_selector():
    group = _normalize_atom_group(
        {"selector": {"elements": ["O", "S"]}, "name": "chalcogens"},
        existing_ids=set(),
    )
    assert group is not None
    assert group["selector"] == {"elements": ["O", "S"]}
    assert group["name"] == "chalcogens"
    # color/color_light default to None when no group rule overrides
    # them; the renderer falls back to the element palette.
    assert group["color"] is None


def test_normalize_atom_group_rejects_empty_selector():
    assert (
        _normalize_atom_group({"selector": {}}, existing_ids=set()) is None
    )
    assert (
        _normalize_atom_group({"selector": {"unknown": "key"}}, existing_ids=set()) is None
    )
    assert _normalize_atom_group({}, existing_ids=set()) is None
    assert _normalize_atom_group("not a dict", existing_ids=set()) is None


def test_normalize_atom_group_clamps_opacity_and_validates_choices():
    group = _normalize_atom_group(
        {
            "selector": {"all": True},
            "opacity": 5.0,
            "material": "bogus",
            "style": "ortep",
        },
        existing_ids=set(),
    )
    assert group is not None
    assert group["opacity"] == 1.0  # clamped
    assert group["material"] is None  # invalid choice -> None
    assert group["style"] == "ortep"


def test_normalize_atom_groups_replaces_duplicate_ids():
    groups = _normalize_atom_groups(
        [
            {"id": "dup", "selector": {"all": True}},
            {"id": "dup", "selector": {"elements": ["H"]}},
        ]
    )
    assert len(groups) == 2
    assert groups[0]["id"] == "dup"
    assert groups[1]["id"] != "dup"


# ---- backend CRUD ---------------------------------------------------------


def test_add_atom_group_persists_to_active_scene(backend: ViewerBackend):
    group = backend.add_atom_group(
        selector={"elements": ["O"]},
        color="#FF0000",
        name="oxygen-red",
    )
    assert group["color"] == "#ff0000"
    assert group["selector"] == {"elements": ["O"]}

    state = backend.get_state()
    assert any(item["id"] == group["id"] for item in state["atom_groups"])


def test_add_atom_group_rejects_bad_selector(backend: ViewerBackend):
    with pytest.raises(ValueError):
        backend.add_atom_group(selector={})


def test_update_atom_group_overrides_fields(backend: ViewerBackend):
    group = backend.add_atom_group(selector={"elements": ["O"]}, color="#FF0000")
    updated = backend.update_atom_group(
        group["id"],
        {"color": "#00FF00", "visible": False, "opacity": 0.4, "material": "flat"},
    )
    assert updated["color"] == "#00ff00"
    assert updated["visible"] is False
    assert updated["opacity"] == pytest.approx(0.4)
    assert updated["material"] == "flat"
    assert updated["id"] == group["id"]


def test_update_atom_group_unknown_id_raises(backend: ViewerBackend):
    with pytest.raises(KeyError):
        backend.update_atom_group("nope", {"color": "#000000"})


def test_remove_atom_group(backend: ViewerBackend):
    group = backend.add_atom_group(selector={"all": True})
    assert backend.remove_atom_group(group["id"]) is True
    assert backend.list_atom_groups() == []
    assert backend.remove_atom_group(group["id"]) is False


def test_reorder_atom_groups_requires_full_set(backend: ViewerBackend):
    a = backend.add_atom_group(selector={"all": True})
    b = backend.add_atom_group(selector={"elements": ["O"]})
    backend.reorder_atom_groups([b["id"], a["id"]])
    assert [g["id"] for g in backend.list_atom_groups()] == [b["id"], a["id"]]
    with pytest.raises(ValueError):
        backend.reorder_atom_groups([a["id"]])


# ---- style_for_state ------------------------------------------------------


def test_style_for_state_exports_atom_groups_to_renderer(backend: ViewerBackend):
    group = backend.add_atom_group(
        selector={"elements": ["H"]}, color="#FFFFFF", visible=False
    )
    state = backend.get_state()
    style = backend.style_for_state(state)
    assert isinstance(style.get("atom_groups"), list)
    assert any(g["id"] == group["id"] for g in style["atom_groups"])
