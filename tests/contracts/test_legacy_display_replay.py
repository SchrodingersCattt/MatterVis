"""Legacy display supercell and transform ordering. T02, T03."""

from __future__ import annotations

import json
from pathlib import Path

from mat_viewer.app import ViewerBackend

UREA = Path("docs/qa/urea-default.cif")


def _backend(tmp_path: Path) -> ViewerBackend:
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps({"structures": {"urea": {"cif_path": str(UREA.resolve()), "title": "urea"}}}),
        encoding="utf-8",
    )
    return ViewerBackend(preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path), names=["urea"])


def test_t02_supercell_shorthand_replaces_and_clears_only_repeat(tmp_path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "supercell": {"a": 2, "b": 2, "c": 2}},
            scene_id=scene_id,
            broadcast=False,
        )
        first = backend.scene_for_state(backend.get_state(scene_id))
        raw_before = len(backend.get_bundle("urea").raw_atoms)
        bundle_scene_atoms = len(backend.get_bundle("urea").scene.get("draw_atoms") or [])
        backend.patch_state({"supercell": {"a": 2, "b": 2, "c": 2}}, scene_id=scene_id, broadcast=False)
        second = backend.scene_for_state(backend.get_state(scene_id))
        assert len(second.get("draw_atoms") or []) == len(first.get("draw_atoms") or [])
        repeats = [item for item in backend.list_transforms(scene_id) if item["kind"] == "repeat"]
        assert len(repeats) == 1
        assert repeats[0]["params"] == {"a": 2, "b": 2, "c": 2}
        backend.add_transform("grow_radius", {"radius": 2.0, "seeds": {"all": True}}, scene_id=scene_id, auto_promote=False)
        backend.patch_state({"supercell": {"a": 1, "b": 1, "c": 1}}, scene_id=scene_id, broadcast=False)
        kinds = [item["kind"] for item in backend.list_transforms(scene_id)]
        assert "repeat" not in kinds
        assert "grow_radius" in kinds
        assert len(backend.get_bundle("urea").raw_atoms) == raw_before
        assert len(backend.get_bundle("urea").scene.get("draw_atoms") or []) == bundle_scene_atoms
        assert backend.effect_session().counters.commits == 0 if hasattr(backend, "_effect_session") else True
    finally:
        backend.close(wait=False)


def test_t03_explicit_transform_list_keeps_order(tmp_path):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state({"display_mode": "unit_cell"}, scene_id=scene_id, broadcast=False)
        first = backend.add_transform("repeat", {"a": 2, "b": 1, "c": 1}, scene_id=scene_id, replace_repeat=False)
        second = backend.add_transform("repeat", {"a": 1, "b": 2, "c": 1}, scene_id=scene_id, replace_repeat=False)
        order = [item["id"] for item in backend.list_transforms(scene_id)]
        assert order == [first["id"], second["id"]]
        backend.reorder_transforms([second["id"], first["id"]], scene_id=scene_id)
        assert [item["id"] for item in backend.list_transforms(scene_id)] == [second["id"], first["id"]]
        backend.update_transform(first["id"], {"enabled": False}, scene_id=scene_id)
        assert backend.list_transforms(scene_id)[1]["enabled"] is False
    finally:
        backend.close(wait=False)


def test_by_symmetry_stays_explicitly_unsupported(tmp_path):
    backend = _backend(tmp_path)
    try:
        try:
            backend.add_transform("by_symmetry", {"ops": []}, scene_id=backend.active_scene_id())
        except NotImplementedError:
            pass
        else:
            raise AssertionError("by_symmetry")
    finally:
        backend.close(wait=False)
