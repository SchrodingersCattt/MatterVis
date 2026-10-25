"""Planned structural operations are not offered as if they existed."""

from __future__ import annotations

from pathlib import Path

from mat_viewer.app import ViewerBackend
from mat_viewer.app.normalizers import _TRANSFORM_KIND_NAMES, _normalize_transform
from mat_viewer.presets import default_preset_path


def test_add_menu_hides_symmetry_while_existing_rows_still_normalize():
    assert "by_symmetry" not in _TRANSFORM_KIND_NAMES
    assert "repeat" in _TRANSFORM_KIND_NAMES
    row = _normalize_transform(
        {
            "id": "old",
            "kind": "by_symmetry",
            "name": "Apply symmetry",
            "params": {"seeds": {"all": True}, "ops": []},
            "enabled": False,
        },
        existing_ids=set(),
    )
    assert row is not None
    assert row["kind"] == "by_symmetry"
    assert row["name"] == "Apply symmetry"
    assert row["enabled"] is False


def test_repeat_and_slab_do_not_point_at_a_structural_section(tmp_path: Path):
    backend = ViewerBackend(
        preset_path=default_preset_path(), root_dir=str(tmp_path)
    )
    try:
        backend.patch_state({"display_mode": "unit_cell"}, broadcast=False)
        repeat = backend.add_transform("repeat", {"a": 2, "b": 1, "c": 1})
        slab = backend.add_transform("slab", {"miller": [1, 0, 0], "vacuum": 10.0})
        text = " ".join(
            list(repeat.get("warnings") or []) + list(slab.get("warnings") or [])
        )
        assert "Structural" not in text
        assert "structural_operation_kinds" not in dir(backend)
    finally:
        backend.close(wait=False)
