"""Regressions for QA items 6, 8, 10, and 37 on issue #175."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from dash import no_update

from mat_viewer.app import ViewerBackend, callbacks_disorder, callbacks_operations, callbacks_state

DAP4 = Path("scripts/data/DAP-4.cif")


def _backend(tmp_path: Path, cif: Path, name: str) -> ViewerBackend:
    local = tmp_path / ".local"
    local.mkdir(parents=True, exist_ok=True)
    (local / "catalog.local.json").write_text(
        json.dumps(
            {
                "structures": {
                    name: {"cif_path": str(cif.resolve()), "title": name},
                }
            }
        ),
        encoding="utf-8",
    )
    return ViewerBackend(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        names=[name],
    )


def _home(atoms):
    return [
        atom
        for atom in atoms
        if not atom.get("_is_boundary_replica")
        and not atom.get("_is_framework_context_replica")
        and not atom.get("_is_bonded_image_replica")
    ]


def test_repeat_presets_differ_and_supercell_bonds_stay_short(tmp_path: Path):
    backend = _backend(tmp_path, DAP4, "DAP-4")
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "display_options": ["hydrogens", "unit_cell_box"]},
            scene_id=scene_id,
            broadcast=False,
        )
        base = backend.scene_for_state(backend.get_state(scene_id))
        home = _home(base.get("draw_atoms") or [])
        backend.add_transform(
            "repeat", {"a": 2, "b": 2, "c": 2}, scene_id=scene_id, replace_repeat=True
        )
        two = backend.scene_for_state(backend.get_state(scene_id))
        two_atoms = two.get("draw_atoms") or []
        assert len(two_atoms) == len(home) * 8
        lengths = [
            float(np.linalg.norm(np.asarray(bond["end"]) - np.asarray(bond["start"])))
            for bond in (two.get("bonds") or [])
        ]
        assert lengths
        assert max(lengths) < 3.5
        backend.add_transform(
            "repeat", {"a": 3, "b": 3, "c": 3}, scene_id=scene_id, replace_repeat=True
        )
        three = backend.scene_for_state(backend.get_state(scene_id))
        assert len(three.get("draw_atoms") or []) == len(home) * 27
        repeats = [item for item in backend.list_transforms(scene_id) if item["kind"] == "repeat"]
        assert len(repeats) == 1
        assert repeats[0]["params"] == {"a": 3, "b": 3, "c": 3}
    finally:
        backend.close()


class _Registry:
    def __init__(self):
        self.callbacks = {}

    def callback(self, *args, **kwargs):
        def register(fn):
            self.callbacks[fn.__name__] = fn
            return fn

        return register

    def clientside_callback(self, *args, **kwargs):
        return None


def _manage(backend, monkeypatch, **overrides):
    registry = _Registry()
    callbacks_operations.register_operations_callbacks(registry, backend)
    monkeypatch.setattr(
        callbacks_operations,
        "callback_context",
        SimpleNamespace(triggered_id=overrides.pop("triggered_id"), triggered=[]),
    )
    fn = registry.callbacks["manage_transforms"]
    args = dict(
        add_clicks=None,
        preset_2x_clicks=None,
        preset_3x_clicks=None,
        clear_repeat_clicks=None,
        clear_all_clicks=None,
        active_scene_id=backend.active_scene_id(),
        enableds=[],
        deletes=[],
        ups=[],
        downs=[],
        param_a=[],
        param_b=[],
        param_c=[],
        param_seeds=[],
        param_radius=[],
        param_hops=[],
        param_maxhops=[],
        param_cutoff=[],
        param_ops=[],
        param_miller0=[],
        param_miller1=[],
        param_miller2=[],
        param_layers=[],
        param_vacuum=[],
        kind_select="repeat",
        enabled_ids=[],
    )
    args.update(overrides)
    return fn(**args)


def test_clear_all_ignores_stale_pattern_rows(tmp_path: Path, monkeypatch):
    backend = _backend(tmp_path, DAP4, "DAP-4")
    try:
        scene_id = backend.active_scene_id()
        spec = backend.add_transform("repeat", {"a": 2, "b": 2, "c": 2}, scene_id=scene_id)
        row_id = {
            "type": "trf-row-enabled",
            "scene_id": scene_id,
            "transform_id": spec["id"],
        }
        _manage(
            backend,
            monkeypatch,
            triggered_id="transforms-clear-btn",
            clear_all_clicks=1,
            enableds=[["yes"]],
            enabled_ids=[row_id],
            param_a=[2],
            param_b=[2],
            param_c=[2],
        )
        assert backend.list_transforms(scene_id) == []
        _manage(
            backend,
            monkeypatch,
            triggered_id={"type": "trf-param-a", "scene_id": scene_id, "transform_id": spec["id"]},
            enableds=[["yes"]],
            enabled_ids=[row_id],
            param_a=[2],
            param_b=[2],
            param_c=[2],
        )
        assert backend.list_transforms(scene_id) == []
        other = backend.create_scene(structure="DAP-4", label="Copy")
        backend.add_transform("repeat", {"a": 2, "b": 2, "c": 2}, scene_id=other["id"])
        foreign = {
            "type": "trf-row-enabled",
            "scene_id": scene_id,
            "transform_id": backend.list_transforms(other["id"])[0]["id"],
        }
        _manage(
            backend,
            monkeypatch,
            triggered_id={"type": "trf-param-a", "scene_id": scene_id, "transform_id": foreign["transform_id"]},
            active_scene_id=other["id"],
            enableds=[["yes"]],
            enabled_ids=[foreign],
            param_a=[9],
            param_b=[9],
            param_c=[9],
        )
        assert backend.list_transforms(other["id"])[0]["params"]["a"] == 2
    finally:
        backend.close()


def test_wrap_into_cell_is_display_only(tmp_path: Path):
    backend = _backend(tmp_path, DAP4, "DAP-4")
    try:
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {"display_mode": "unit_cell", "display_options": ["hydrogens"]},
            scene_id=scene_id,
            broadcast=False,
        )
        bundle = backend.get_bundle("DAP-4")
        source_before = [
            np.asarray(atom.get("frac"), dtype=float).copy()
            for atom in bundle.raw_atoms
            if atom.get("frac") is not None
        ]
        open_scene = backend.scene_for_state(backend.get_state(scene_id))
        outside = [
            atom
            for atom in open_scene.get("draw_atoms") or []
            if atom.get("frac") is not None
            and np.any(
                (np.asarray(atom["frac"], dtype=float) < -1e-6)
                | (np.asarray(atom["frac"], dtype=float) >= 1.0 + 1e-6)
            )
        ]
        assert outside
        backend.patch_state(
            {"display_options": ["hydrogens", "wrap_into_cell", "unit_cell_box"]},
            scene_id=scene_id,
            broadcast=False,
        )
        wrapped = backend.scene_for_state(backend.get_state(scene_id))
        fracs = [
            np.asarray(atom["frac"], dtype=float)
            for atom in wrapped.get("draw_atoms") or []
            if atom.get("frac") is not None
        ]
        assert fracs
        assert all(np.all((frac >= -1e-6) & (frac < 1.0 + 1e-6)) for frac in fracs)
        assert not any(
            atom.get("_is_boundary_replica") or atom.get("_is_bonded_image_replica")
            for atom in wrapped.get("draw_atoms") or []
        )
        source_after = [
            np.asarray(atom.get("frac"), dtype=float).copy()
            for atom in bundle.raw_atoms
            if atom.get("frac") is not None
        ]
        assert len(source_before) == len(source_after)
        assert all(
            np.allclose(left, right)
            for left, right in zip(source_before, source_after, strict=True)
        )
        fig, _ = backend.figure_for_state(backend.get_state(scene_id))
        names = [str(getattr(trace, "name", "") or "") for trace in fig.data]
        assert "unit-cell-box" in names
    finally:
        backend.close()


def test_scope_capture_does_not_write_another_scene(tmp_path: Path, monkeypatch):
    backend = _backend(tmp_path, DAP4, "DAP-4")
    try:
        first = backend.active_scene_id()
        registry = _Registry()
        callbacks_state.register_state_callbacks(registry, backend)
        second = backend.create_scene(structure="DAP-4", label="Second")["id"]
        backend.patch_state({"display_mode": "formula_unit"}, scene_id=second, broadcast=False)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "display-mode-selector.value"}]),
        )
        result = registry.callbacks["capture_state"](
            second,
            "cluster",
            ["axes"],
            1.0,
            0.15,
            0.35,
            "mesh",
            "ball_stick",
            "opacity",
            "ortep_solid",
            0.14,
            None,
            [],
            [],
            "auto",
            None,
            "viridis",
            "auto",
            None,
            None,
            None,
            "#BDBDBD",
            ["show"],
        )
        assert result == (no_update, no_update, no_update)
        assert backend.get_state(second)["display_mode"] == "formula_unit"
        assert first != second
    finally:
        backend.close()


def test_tab_switch_restores_that_scenes_resolve_rows(tmp_path: Path, monkeypatch):
    backend = _backend(tmp_path, DAP4, "DAP-4")
    try:
        first = backend.active_scene_id()
        replica = {"id": "rep-1", "label": "Replica A", "kept_count": 4, "added_indices": [], "dropped_indices": []}
        backend.commit_patch(
            {
                "disorder_resolve": {"method": "random", "count": 3, "seed": 7},
                "disorder_replicas": [replica],
            },
            scene_id=first,
            broadcast=False,
        )
        second = backend.create_scene(structure="DAP-4", label="Second")["id"]
        registry = _Registry()
        callbacks_disorder.register_disorder_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_disorder,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "scene-tabs.value"}]),
        )
        switched = registry.callbacks["clear_disorder_rows_on_scene_change"](
            second,
            {"scene_id": first, "replicas": [replica]},
        )
        assert switched[0]["scene_id"] == second
        assert switched[0]["replicas"] == []
        assert switched[2] == "enumerate"
        restored = registry.callbacks["clear_disorder_rows_on_scene_change"](first, switched[0])
        assert restored[0]["replicas"][0]["id"] == "rep-1"
        assert restored[2] == "random"
        assert restored[3] == 3
        assert restored[4] == 7
        leaked = registry.callbacks["persist_resolve_inputs"](
            "optimal",
            9,
            1,
            second,
            {"scene_id": first, "replicas": [replica]},
        )
        assert leaked is no_update
        assert backend.get_state(second).get("disorder_resolve", {}).get("method") != "optimal"
    finally:
        backend.close()


def test_tab_switch_refits_a_camera_that_clips_the_cell_box(tmp_path: Path, monkeypatch):
    backend = _backend(tmp_path, DAP4, "DAP-4")
    try:
        first = backend.active_scene_id()
        zoomed = {
            "eye": {"x": 0.05, "y": 0.02, "z": 0.02},
            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
            "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            "projection": {"type": "perspective"},
        }
        backend.patch_state(
            {
                "display_mode": "unit_cell",
                "display_options": ["unit_cell_box", "hydrogens"],
                "camera": zoomed,
            },
            scene_id=first,
            broadcast=False,
        )
        second = backend.create_scene(structure="DAP-4", label="Other")["id"]
        registry = _Registry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(triggered=[{"prop_id": "scene-tabs.value"}]),
        )
        before = int(backend.get_state(first).get("camera_revision", 0) or 0)
        result = registry.callbacks["sync_agent_state"](None, None, None, first)
        state = backend.get_state(first)
        assert result[-3]["scene_id"] == first
        assert state["camera"]["eye"] != zoomed["eye"]
        assert int(state["camera_revision"]) > before
        assert second != first
    finally:
        backend.close()
