"""Persistence, export, and legacy migration. T16, T20, T21."""

from __future__ import annotations

import json

from effect_builders import f1_ordered_nonorthogonal, f6_frames
from mat_viewer.effects import EffectSession
from mat_viewer.effects.errors import MigrationError
from mat_viewer.effects.persistence import export_structure, load_session, migrate_legacy_session, save_session


def test_t20_save_reload_and_export_do_not_follow_display_state(tmp_path):
    session = EffectSession()
    ref = session.register(
        f1_ordered_nonorthogonal(),
        structure_id="cell",
        properties={"charges": [0.3, -0.3]},
    )
    session.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        structure_id=ref.id,
        expected_revision=1,
        property_policy={"charges": {"action": "transform"}},
    )
    session.note_display_edit()
    path = tmp_path / "session.json"
    save_session(session, str(path))
    restored = load_session(str(path))
    assert restored.fingerprint(restored.current_ref("cell")) == session.fingerprint(session.current_ref("cell"))
    assert restored.snapshot(restored._frames[("cell", "0")][0].ref).n_sites == 2
    exported = export_structure(
        session.snapshot(session.current_ref("cell")),
        session.checkout(session.current_ref("cell")),
        fmt="cif",
    )
    assert any("property" in warning for warning in exported["warnings"])
    internal = export_structure(session.snapshot(ref), session.checkout(ref), fmt="internal")
    assert json.loads(internal["text"])["n_sites"] == 2


def test_t21_legacy_migration_is_idempotent_and_strict(tmp_path):
    legacy = {
        "version": 1,
        "active_id": "scene-a",
        "order": ["scene-a", "scene-b"],
        "scenes": [
            {
                "id": "scene-a",
                "label": "Bulk",
                "structure_name": "urea",
                "state_patch": {
                    "transforms": [{"id": "r", "kind": "repeat", "params": {"a": 2, "b": 2, "c": 2}, "enabled": True}],
                    "polyhedron_specs": [{"id": "poly", "color": "#112233"}],
                    "disorder_replicas": [{"id": "cand"}],
                },
                "camera": {"position": [1, 2, 3]},
            },
            {
                "id": "scene-b",
                "label": "Copy",
                "structure_name": "urea",
                "state_patch": {"display_mode": "unit_cell"},
            },
        ],
    }
    path = tmp_path / "scenes.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    first = migrate_legacy_session(str(path), known_structures={"urea"})
    assert first["structural_commits"] == 0
    assert (tmp_path / "scenes.json.bak").is_file()
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["scenes"][0]["view"]["transforms"][0]["kind"] == "repeat"
    assert migrated["scenes"][0]["view"]["polyhedron_specs"][0]["id"] == "poly"
    second = migrate_legacy_session(str(path), known_structures={"urea"})
    assert second["status"] == "already-migrated"
    assert second["structural_commits"] == 0
    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    try:
        migrate_legacy_session(str(unknown), known_structures=set())
    except MigrationError:
        pass
    else:
        raise AssertionError("unknown schema")
    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps({"version": 1, "scenes": [{"id": "x", "structure_name": "gone", "state_patch": {}}]}), encoding="utf-8")
    try:
        migrate_legacy_session(str(missing), known_structures={"urea"})
    except MigrationError:
        pass
    else:
        raise AssertionError("missing structure")
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    try:
        migrate_legacy_session(str(corrupt))
    except MigrationError:
        pass
    else:
        raise AssertionError("corrupt")


def test_property_arrays_survive_reload_for_both_frames(tmp_path):
    first, second = f6_frames()
    session = EffectSession()
    session.register(first, structure_id="traj", frame_ref="0", properties={"charges": [1.0, -1.0]})
    session.register(second, structure_id="traj", frame_ref="1", properties={"charges": [2.0, -2.0]})
    path = tmp_path / "frames.json"
    save_session(session, str(path))
    restored = load_session(str(path))
    assert restored._frames[("traj", "0")][0].properties["site"]["charges"] == (1.0, -1.0)
    assert restored._frames[("traj", "1")][0].properties["site"]["charges"] == (2.0, -2.0)
