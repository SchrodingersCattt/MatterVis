"""Viewer adapter for the single Operate commit boundary.

The adapter pins the scene id before doing scientific work, then
registers a new render bundle only after the session commit succeeds.
Display repetition is not accepted here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effects.materialize import loaded_crystal_from_crystal
from ..effects.session import Candidate, CommitResult, EffectSession


class _OperateBackendMixin:
    def effect_session(self) -> EffectSession:
        session = getattr(self, "_effect_session", None)
        if session is None:
            session = EffectSession()
            self._effect_session = session
        return session

    def operate(
        self,
        command: str,
        params: Mapping[str, Any] | None = None,
        *,
        scene_id: str | None = None,
        expected_revision: int | None = None,
        frame_ref: str | None = None,
        request_id: str | None = None,
        preview: bool = False,
        property_policy: Mapping[str, Any] | None = None,
        inject_failure: str | None = None,
    ) -> dict[str, Any]:
        if str(command) in {"supercell", "operate_supercell"}:
            raise ValueError("use build_supercell; display supercell remains a view repeat")
        pinned_scene = scene_id or self.active_scene_id()
        state = self.get_state(pinned_scene)
        bundle_name = str(state.get("structure") or "")
        bundle = self.get_bundle(bundle_name)
        session = self.effect_session()
        structure_id = str(getattr(bundle, "scientific_structure_id", None) or bundle.name)
        frame = str(frame_ref if frame_ref is not None else getattr(bundle, "scientific_frame", "0"))
        if not session.has(structure_id, frame):
            crystal = getattr(bundle, "crystal", None)
            if crystal is None:
                raise ValueError(f"{bundle_name} has no molecular crystal to edit")
            session.register(
                crystal,
                structure_id=structure_id,
                frame_ref=frame,
                provenance={"viewer_bundle": bundle.name, "cif_path": getattr(bundle, "cif_path", "")},
                viewer_name=bundle.name,
            )
            bundle.scientific_structure_id = structure_id
            bundle.scientific_revision = 1
            bundle.scientific_frame = frame
        pinned_revision = int(
            expected_revision
            if expected_revision is not None
            else getattr(bundle, "scientific_revision", None)
            or session.current_ref(structure_id, frame).revision
        )
        outcome = session.operate(
            str(command),
            params or {},
            structure_id=structure_id,
            expected_revision=pinned_revision,
            frame_ref=frame,
            request_id=request_id,
            preview=preview,
            property_policy=property_policy,
            inject_failure=inject_failure,
        )
        if isinstance(outcome, Candidate):
            payload = outcome.to_dict()
            payload["scene_id"] = pinned_scene
            payload["effect"] = "preview"
            return payload
        if not isinstance(outcome, CommitResult):
            raise TypeError("operate returned an unknown result")
        payload = outcome.to_dict()
        payload["source_scene_id"] = pinned_scene
        if outcome.idempotent_replay:
            payload["scene_id"] = self._scene_for_bundle(outcome.viewer_name) or pinned_scene
            return payload
        scene = self._publish_committed_structure(outcome, source_label=str(state.get("scene_label") or bundle_name))
        payload["scene_id"] = scene["id"]
        return payload

    def commit_operation(
        self,
        candidate_id: str,
        *,
        expected_revision: int,
        request_id: str | None = None,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        pinned_scene = scene_id or self.active_scene_id()
        state = self.get_state(pinned_scene)
        outcome = self.effect_session().commit_candidate(
            candidate_id,
            expected_revision=int(expected_revision),
            request_id=request_id,
        )
        payload = outcome.to_dict()
        payload["source_scene_id"] = pinned_scene
        scene = self._publish_committed_structure(
            outcome,
            source_label=str(state.get("scene_label") or outcome.output_ref.id),
        )
        payload["scene_id"] = scene["id"]
        return payload

    def undo_structure(self, structure_id: str | None = None, *, scene_id: str | None = None) -> dict[str, Any]:
        pinned_scene = scene_id or self.active_scene_id()
        state = self.get_state(pinned_scene)
        bundle = self.get_bundle(str(state.get("structure") or ""))
        target = structure_id or str(getattr(bundle, "scientific_structure_id", None) or bundle.name)
        frame = str(getattr(bundle, "scientific_frame", "0"))
        ref = self.effect_session().undo(target, frame)
        return {"structure": ref.to_dict(), "scene_id": pinned_scene, "effect": "undo"}

    def redo_structure(self, structure_id: str | None = None, *, scene_id: str | None = None) -> dict[str, Any]:
        pinned_scene = scene_id or self.active_scene_id()
        state = self.get_state(pinned_scene)
        bundle = self.get_bundle(str(state.get("structure") or ""))
        target = structure_id or str(getattr(bundle, "scientific_structure_id", None) or bundle.name)
        frame = str(getattr(bundle, "scientific_frame", "0"))
        ref = self.effect_session().redo(target, frame)
        return {"structure": ref.to_dict(), "scene_id": pinned_scene, "effect": "redo"}

    def _scene_for_bundle(self, bundle_name: str | None) -> str | None:
        if not bundle_name:
            return None
        for scene_id, scene in self.scene_store.scenes.items():
            if scene.structure_name == bundle_name:
                return str(scene_id)
        return None

    def _publish_committed_structure(self, outcome: CommitResult, *, source_label: str) -> dict[str, Any]:
        name = str(outcome.viewer_name or f"{outcome.output_ref.id}@{outcome.output_ref.revision}")
        existing = self._scene_for_bundle(name)
        if existing is not None:
            return {"id": existing}
        crystal = self.effect_session().checkout(outcome.output_ref)
        built = loaded_crystal_from_crystal(
            crystal,
            name=name,
            title=f"{source_label} {outcome.command}",
        )
        built.scientific_structure_id = outcome.output_ref.id
        built.scientific_revision = outcome.output_ref.revision
        built.scientific_frame = outcome.output_ref.frame_ref
        with self._lock:
            self.bundles[name] = built
            if name not in self.structure_names:
                self.structure_names.append(name)
            self._drop_placeholder()
            scene = self.create_scene(structure=name, label=built.title)
        # A committed structure opens on its own cell, without the source view's repeat.
        self.patch_state({"transforms": [], "display_mode": "unit_cell"}, scene_id=scene["id"], broadcast=False)
        return scene


__all__ = ["_OperateBackendMixin"]
