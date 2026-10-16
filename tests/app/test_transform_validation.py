"""Regression coverage for invalid Operation-panel transform inputs.

The editor must reject malformed values at the callback boundary and route
the exception through ``surface_callback_error``.  Otherwise an enabled row
quietly stores a coerced empty value and the render worker fails later, which
looks like an unresponsive Apply button.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from mat_viewer.app import ViewerBackend
from mat_viewer.app.callbacks_operations import _build_transforms
from mat_viewer.presets import default_preset_path


def _builder_kwargs(**overrides):
    values = dict(
        enabled_ids=[{"transform_id": "t1"}],
        enableds=[["yes"]],
        param_a=[None],
        param_b=[None],
        param_c=[None],
        param_seeds=[None],
        param_radius=[None],
        param_hops=[None],
        param_maxhops=[None],
        param_cutoff=[None],
        param_ops=[None],
        param_miller0=[None],
        param_miller1=[None],
        param_miller2=[None],
        param_layers=[None],
        param_vacuum=[None],
        existing={
            "t1": {
                "name": "test",
                "kind": "repeat",
                "params": {"a": 1, "b": 1, "c": 1},
            }
        },
    )
    values.update(overrides)
    return values


def test_invalid_symmetry_json_is_rejected_at_editor_boundary():
    kwargs = _builder_kwargs(
        param_ops=["{not-json"],
        existing={"t1": {"kind": "by_symmetry", "params": {"ops": []}}},
    )
    with pytest.raises(ValueError, match="valid JSON"):
        _build_transforms(**kwargs)


def test_zero_slab_miller_indices_are_rejected():
    kwargs = _builder_kwargs(
        param_miller0=[0],
        param_miller1=[0],
        param_miller2=[0],
        existing={"t1": {"kind": "slab", "params": {"miller": [0, 0, 1]}}},
    )
    with pytest.raises(ValueError, match="cannot all be zero"):
        _build_transforms(**kwargs)


def test_unknown_element_seed_is_rejected():
    kwargs = _builder_kwargs(
        param_seeds=["elem:Zz"],
        existing={"t1": {"kind": "grow_radius", "params": {"seeds": {"all": True}}}},
    )
    with pytest.raises(ValueError, match="unknown element"):
        _build_transforms(**kwargs)


def test_backend_rejects_apply_symmetry_before_render(tmp_path: Path):
    backend = ViewerBackend(
        preset_path=default_preset_path(), root_dir=str(tmp_path)
    )
    try:
        with pytest.raises(NotImplementedError, match="Apply symmetry"):
            backend.add_transform(
                "by_symmetry",
                {"seeds": {"all": True}, "ops": []},
            )
        assert backend.list_transforms() == []
    finally:
        backend.close(wait=False)


def test_api_surfaces_apply_symmetry_not_implemented(tmp_path: Path):
    from mat_viewer.app import create_app

    app = create_app(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        response = app.server.test_client().post(
            "/api/v2/transforms",
            json={"kind": "by_symmetry", "params": {"ops": []}},
        )
        assert response.status_code == 400
        assert "Apply symmetry" in response.get_json()["error"]
    finally:
        app.crystal_backend.close(wait=False)


def test_backend_rejects_zero_slab_before_render(tmp_path: Path):
    backend = ViewerBackend(
        preset_path=default_preset_path(), root_dir=str(tmp_path)
    )
    try:
        with pytest.raises(ValueError, match="cannot all be zero"):
            backend.add_transform("slab", {"miller": [0, 0, 0]})
        assert backend.list_transforms() == []
    finally:
        backend.close(wait=False)


def test_backend_surfaces_repeat_atom_budget_error(tmp_path: Path, monkeypatch):
    backend = ViewerBackend(
        preset_path=default_preset_path(), root_dir=str(tmp_path)
    )
    try:
        monkeypatch.setattr(
            backend,
            "scene_for_state",
            lambda _state: {"draw_atoms": [{}, {}]},
        )
        with pytest.raises(ValueError, match="MAX_ATOMS_AFTER_TRANSFORM"):
            backend.add_transform("repeat", {"a": 1000, "b": 1000, "c": 1000})
        assert backend.list_transforms() == []
    finally:
        backend.close(wait=False)


def test_operation_callback_surfaces_invalid_json_and_keeps_rows(tmp_path: Path, monkeypatch):
    """An invalid row edit writes the visible status and leaves the table."""
    from mat_viewer.app import shared

    app = __import__("mat_viewer.app", fromlist=["create_app"]).create_app(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    backend = app.crystal_backend
    try:
        spec = backend.add_transform(
            "by_symmetry", {"seeds": {"all": True}, "ops": []}, enabled=False
        )
        captured: list[tuple[str, dict]] = []

        class _StatusContext:
            triggered_id: ClassVar[dict[str, str]] = {
                "type": "trf-param-ops",
                "transform_id": spec["id"],
            }

            @staticmethod
            def set_props(component_id, props):
                captured.append((component_id, dict(props)))

        monkeypatch.setattr(shared, "callback_context", _StatusContext)
        from mat_viewer.app import callbacks_operations

        monkeypatch.setattr(callbacks_operations, "callback_context", _StatusContext)
        callback = next(
            value["callback"]
            for key, value in app.callback_map.items()
            if "transforms-rows-container" in key
        )
        manage_transforms = getattr(callback, "__wrapped__", callback)
        result = manage_transforms(
            add_clicks=None,
            preset_2x_clicks=None,
            preset_3x_clicks=None,
            clear_repeat_clicks=None,
            clear_all_clicks=None,
            active_scene_id=backend.active_scene_id(),
            enableds=[["yes"]],
            deletes=[0],
            ups=[0],
            downs=[0],
            param_a=[None],
            param_b=[None],
            param_c=[None],
            param_seeds=[None],
            param_radius=[None],
            param_hops=[None],
            param_maxhops=[None],
            param_cutoff=[None],
            param_ops=["{not-json"],
            param_miller0=[None],
            param_miller1=[None],
            param_miller2=[None],
            param_layers=[None],
            param_vacuum=[None],
            kind_select="by_symmetry",
            enabled_ids=[{"type": "trf-row-enabled", "transform_id": spec["id"]}],
        )
        assert captured[0][0] == "status"
        assert "valid JSON" in captured[0][1]["children"]
        assert result[0]  # the existing row was rebuilt for correction
        assert backend.list_transforms()[0]["enabled"] is False
    finally:
        backend.close(wait=False)


def test_repeat_preset_replaces_active_repeat(tmp_path: Path):
    backend = ViewerBackend(
        preset_path=default_preset_path(), root_dir=str(tmp_path)
    )
    try:
        backend.add_transform(
            "repeat", {"a": 2, "b": 2, "c": 2}, replace_repeat=True
        )
        backend.add_transform(
            "repeat", {"a": 3, "b": 3, "c": 3}, replace_repeat=True
        )
        repeats = [item for item in backend.list_transforms() if item["kind"] == "repeat"]
        assert len(repeats) == 1
        assert repeats[0]["params"] == {"a": 3, "b": 3, "c": 3}
    finally:
        backend.close(wait=False)
