"""Viewer publication is part of the scientific commit."""

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


def test_bundle_construction_failure_does_not_commit(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    try:
        def boom(*_args, **_kwargs):
            raise RuntimeError("bundle failed")

        monkeypatch.setattr("mat_viewer.app.backend_operate.loaded_crystal_from_crystal", boom)
        scene_id = backend.active_scene_id()
        try:
            backend.operate("build_supercell", {"a": 2, "b": 1, "c": 1}, scene_id=scene_id, expected_revision=1)
        except RuntimeError:
            pass
        else:
            raise AssertionError("bundle failure was ignored")
        session = backend.effect_session()
        assert session.current_ref("urea").revision == 1
        assert session.counters.commits == 0
        assert session.snapshot(session.current_ref("urea")).n_sites == len(backend.get_bundle("urea").raw_atoms)
    finally:
        backend.close(wait=False)


def test_scene_creation_failure_rolls_back_and_retry_publishes(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    try:
        scene_id = backend.active_scene_id()
        raw = len(backend.get_bundle("urea").raw_atoms)
        original = backend.create_scene
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("scene failed")
            return original(*args, **kwargs)

        monkeypatch.setattr(backend, "create_scene", flaky)
        try:
            backend.operate("build_supercell", {"a": 2, "b": 1, "c": 1}, scene_id=scene_id, expected_revision=1)
        except RuntimeError:
            pass
        else:
            raise AssertionError("scene failure was ignored")
        session = backend.effect_session()
        assert session.current_ref("urea").revision == 1
        assert session.counters.commits == 0
        assert all(scene.structure_name == "urea" for scene in backend.scene_store.scenes.values())
        monkeypatch.setattr(backend, "create_scene", original)
        committed = backend.operate(
            "build_supercell",
            {"a": 2, "b": 1, "c": 1},
            scene_id=scene_id,
            expected_revision=1,
        )
        assert session.current_ref("urea").revision == committed["output"]["revision"]
        assert session.current_ref("urea").revision != 1
        published = backend.get_bundle(backend.get_state(committed["scene_id"])["structure"])
        assert len(published.raw_atoms) == raw * 2
        assert published.scientific_revision == session.current_ref("urea").revision
    finally:
        backend.close(wait=False)


def test_closed_viewer_reloads_the_effect_session(tmp_path):
    backend = _backend(tmp_path)
    scene_id = backend.active_scene_id()
    committed = backend.operate(
        "build_supercell",
        {"a": 2, "b": 1, "c": 1},
        scene_id=scene_id,
        expected_revision=1,
    )
    revision = committed["output"]["revision"]
    fingerprint = backend.effect_session().fingerprint(backend.effect_session().current_ref("urea"))
    backend.close(wait=False)
    restored = _backend(tmp_path)
    try:
        session = restored.effect_session()
        assert session.current_ref("urea").revision == revision
        assert session.fingerprint(session.current_ref("urea")) == fingerprint
        names = {scene.structure_name for scene in restored.scene_store.scenes.values()}
        assert any(name.endswith(f"@{revision}") for name in names)
    finally:
        restored.close(wait=False)
