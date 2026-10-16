"""MVF-08: one reducer applies reordered intents, and stale ones conflict."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mat_viewer.app.factory import create_app
from mat_viewer.app.shared import ApiError


def _backend():
    app = create_app()
    return app.crystal_backend


def _merge(displayed, incoming):
    """A late callback response cannot replace a newer committed snapshot."""
    if displayed is None:
        return incoming
    if int(incoming.get("version", 0) or 0) < int(displayed.get("version", 0) or 0):
        return displayed
    return incoming


def test_reordered_slider_editor_click_rest_and_tab_keep_the_last_intent():
    backend = _backend()
    try:
        scenes = list(backend.scene_store.scenes)
        assert scenes
        scene_a = backend.active_scene_id()
        scene_b = next(scene_id for scene_id in scenes if scene_id != scene_a) if len(scenes) > 1 else scene_a
        client = "panel"
        responses = []

        def submit(seq, intent_type, payload, scene_id=scene_a):
            result = backend.apply_intent(
                {
                    "type": intent_type,
                    "client_id": client,
                    "client_seq": seq,
                    "base_version": None,
                    "scene_id": scene_id,
                    "payload": payload,
                }
            )
            responses.append(result["state"])
            return result

        submit(1, "set_style", {"atom_scale": 1.35})
        groups = list(backend.get_state(scene_a).get("atom_groups") or [])
        groups.append({"id": "mvf08", "name": "editor", "selector": {"all": True}, "enabled": True})
        submit(2, "crud_atom_group", {"atom_groups": groups})
        submit(3, "patch_state", {"selection": {"atom_labels": ["H1"], "order": ["H1"], "active_label": "H1"}})
        camera = backend.get_camera(scene_a)
        camera = dict(camera)
        camera["eye"] = {"x": 0.4, "y": 0.8, "z": 1.1}
        submit(4, "set_camera", {"camera": camera})
        if scene_b != scene_a:
            submit(5, "set_active_scene", {"scene_id": scene_b}, scene_id=scene_b)

        with pytest.raises(ApiError) as stale:
            backend.apply_intent(
                {
                    "type": "set_style",
                    "client_id": client,
                    "client_seq": 2,
                    "scene_id": scene_a,
                    "payload": {"atom_scale": 9.0},
                }
            )
        assert stale.value.status_code == 409

        base = backend.version
        backend.apply_intent(
            {
                "type": "set_style",
                "client_id": "other",
                "client_seq": 1,
                "scene_id": scene_a,
                "payload": {"bond_radius": 0.2},
            }
        )
        with pytest.raises(ApiError):
            backend.apply_intent(
                {
                    "type": "set_style",
                    "client_id": "other",
                    "client_seq": 2,
                    "base_version": base,
                    "scene_id": scene_a,
                    "payload": {"bond_radius": 0.9},
                }
            )

        displayed = None
        for payload in reversed(responses):
            displayed = _merge(displayed, payload)
        final = backend.get_state(scene_a)
        assert displayed["version"] >= responses[-1]["version"]
        assert final["atom_scale"] == pytest.approx(1.35)
        assert any(group.get("id") == "mvf08" for group in final.get("atom_groups") or [])
        assert "H1" in (final.get("selection") or {}).get("atom_labels", [])
        assert final["camera"]["eye"]["x"] == pytest.approx(0.4)
        assert final["bond_radius"] != pytest.approx(0.9)
        if scene_b != scene_a:
            assert backend.active_scene_id() == scene_b
    finally:
        backend.close()


def test_concurrent_sequences_cannot_apply_an_older_intent_last():
    backend = _backend()
    try:
        scene_id = backend.active_scene_id()
        barrier = threading.Barrier(2)
        results = {}

        def worker(seq, scale):
            barrier.wait()
            try:
                results[seq] = backend.apply_intent(
                    {
                        "type": "set_style",
                        "client_id": "race",
                        "client_seq": seq,
                        "scene_id": scene_id,
                        "payload": {"atom_scale": scale},
                    }
                )
            except ApiError as exc:
                results[seq] = exc

        threads = [
            threading.Thread(target=worker, args=(1, 1.1)),
            threading.Thread(target=worker, args=(2, 1.8)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert 2 in results and not isinstance(results[2], ApiError)
        assert backend.get_state(scene_id)["atom_scale"] == pytest.approx(1.8)
    finally:
        backend.close()


def test_dash_callbacks_are_not_a_second_patch_writer():
    root = Path(__file__).resolve().parents[2] / "mat_viewer" / "app"
    offenders = [
        name
        for name in (
            "callbacks_analysis.py",
            "callbacks_editors.py",
            "callbacks_operations.py",
            "callbacks_disorder.py",
            "callbacks_state.py",
            "rightclick.py",
        )
        if ".patch_state(" in (root / name).read_text(encoding="utf-8")
    ]
    assert offenders == []
