from __future__ import annotations

import copy
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from .presets import json_safe


SCENE_STORE_FILENAME = "crystal_view_scenes.json"


def _now() -> float:
    return float(time.time())


def _scene_id() -> str:
    return f"scene_{uuid.uuid4().hex[:12]}"


@dataclass
class Scene:
    id: str
    label: str
    structure_name: str
    state_patch: dict[str, Any] = field(default_factory=dict)
    camera: dict[str, Any] | None = None
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    @classmethod
    def create(
        cls,
        *,
        label: str,
        structure_name: str,
        state_patch: dict[str, Any] | None = None,
        camera: dict[str, Any] | None = None,
        scene_id: str | None = None,
    ) -> "Scene":
        ts = _now()
        return cls(
            id=scene_id or _scene_id(),
            label=str(label).strip() or str(structure_name),
            structure_name=str(structure_name),
            state_patch=copy.deepcopy(state_patch or {}),
            camera=copy.deepcopy(camera),
            created_at=ts,
            updated_at=ts,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Scene":
        return cls(
            id=str(payload["id"]),
            label=str(payload.get("label") or payload.get("structure_name") or payload["id"]),
            structure_name=str(payload.get("structure_name") or payload.get("structure") or ""),
            state_patch=copy.deepcopy(payload.get("state_patch") or {}),
            camera=copy.deepcopy(payload.get("camera")),
            created_at=float(payload.get("created_at", _now())),
            updated_at=float(payload.get("updated_at", _now())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "structure_name": self.structure_name,
            "state_patch": json_safe(copy.deepcopy(self.state_patch)),
            "camera": json_safe(copy.deepcopy(self.camera)),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def state(self, defaults: dict[str, Any]) -> dict[str, Any]:
        state = copy.deepcopy(defaults)
        state.update(copy.deepcopy(self.state_patch))
        state["structure"] = self.structure_name
        if self.camera is not None:
            state["camera"] = copy.deepcopy(self.camera)
        state["scene_id"] = self.id
        state["scene_label"] = self.label
        return state

    def patch(self, patch: dict[str, Any]) -> None:
        patch = copy.deepcopy(patch)
        if "label" in patch:
            label = str(patch.pop("label")).strip()
            if label:
                self.label = label
        if "structure" in patch:
            self.structure_name = str(patch.pop("structure"))
        if "structure_name" in patch:
            self.structure_name = str(patch.pop("structure_name"))
        if "camera" in patch:
            self.camera = copy.deepcopy(patch.pop("camera"))
        self.state_patch.update(patch)
        self.updated_at = _now()


class SceneStore:
    def __init__(self, path: str):
        self.path = path
        self.scenes: dict[str, Scene] = {}
        self.order: list[str] = []
        self.active_id: str | None = None

    @classmethod
    def default_path(cls, root_dir: str) -> str:
        return os.path.join(root_dir, ".local", SCENE_STORE_FILENAME)

    @classmethod
    def load(cls, path: str) -> "SceneStore":
        store = cls(path)
        if not os.path.exists(path):
            return store
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        for item in raw.get("scenes", []):
            scene = Scene.from_dict(item)
            if scene.id in store.scenes:
                continue
            store.scenes[scene.id] = scene
            store.order.append(scene.id)
        raw_order = [str(item) for item in raw.get("order", [])]
        if raw_order and set(raw_order) == set(store.scenes):
            store.order = raw_order
        active_id = raw.get("active_id")
        store.active_id = str(active_id) if active_id in store.scenes else (store.order[0] if store.order else None)
        return store

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        payload = {
            "version": 1,
            "active_id": self.active_id,
            "order": list(self.order),
            "scenes": [self.scenes[scene_id].to_dict() for scene_id in self.order],
        }
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(json_safe(payload), handle, indent=2, ensure_ascii=False)

    def ensure(self, structures: Iterable[str], *, default_state_factory=None) -> None:
        structures = [str(item) for item in structures]
        if self.order:
            return
        for structure in structures:
            defaults = default_state_factory(structure) if default_state_factory else {"structure": structure}
            self.add(
                label=structure,
                structure_name=structure,
                state_patch=defaults,
                camera=defaults.get("camera") if isinstance(defaults, dict) else None,
                save=False,
            )
        if self.order:
            self.active_id = self.order[0]

    def list(self) -> list[dict[str, Any]]:
        return [self.scenes[scene_id].to_dict() for scene_id in self.order]

    def get(self, scene_id: str | None = None) -> Scene:
        resolved = scene_id or self.active_id
        if resolved is None or resolved not in self.scenes:
            raise KeyError(resolved or "active")
        return self.scenes[resolved]

    def add(
        self,
        *,
        label: str,
        structure_name: str,
        state_patch: dict[str, Any] | None = None,
        camera: dict[str, Any] | None = None,
        scene_id: str | None = None,
        save: bool = True,
    ) -> Scene:
        scene = Scene.create(
            label=label,
            structure_name=structure_name,
            state_patch=state_patch,
            camera=camera,
            scene_id=scene_id,
        )
        while scene.id in self.scenes:
            scene.id = _scene_id()
        if any(item.label == scene.label for item in self.scenes.values()):
            scene.label = self._unique_label(scene.label)
        self.scenes[scene.id] = scene
        self.order.append(scene.id)
        self.active_id = scene.id
        if save:
            self.save()
        return scene

    def remove(self, scene_id: str, *, save: bool = True) -> Scene:
        if scene_id not in self.scenes:
            raise KeyError(scene_id)
        scene = self.scenes.pop(scene_id)
        self.order = [item for item in self.order if item != scene_id]
        if self.active_id == scene_id:
            self.active_id = self.order[0] if self.order else None
        if save:
            self.save()
        return scene

    def prune(self, valid_structures: Iterable[str]) -> list[str]:
        """Drop or repair scenes whose structure pointer is invalid.

        The persisted store can outlive the catalog: an upload from a
        previous session goes to ``tempfile.gettempdir()`` and is
        garbage-collected by the OS, but its scene entry stays on disk;
        a CIF passed via ``--cif`` last time is gone this time. Without
        this prune, ``ViewerBackend.__init__`` resolves the active
        scene, calls ``default_state(structure_name)``, hits an unknown
        structure and raises ``KeyError`` -- the whole app fails to
        boot with a blank page and no UI.

        Returns the list of removed scene ids so the caller can log/save.
        """
        valid = {str(item) for item in valid_structures}
        removed: list[str] = []
        for scene_id, scene in list(self.scenes.items()):
            patch_structure = str(scene.state_patch.get("structure") or "").strip()
            if (
                patch_structure
                and patch_structure in valid
                and patch_structure != scene.structure_name
            ):
                scene.structure_name = patch_structure
                scene.state_patch["structure"] = patch_structure
                scene.updated_at = _now()
            if scene.structure_name not in valid:
                removed.append(scene_id)
                self.scenes.pop(scene_id, None)
        if removed:
            self.order = [sid for sid in self.order if sid in self.scenes]
            if self.active_id not in self.scenes:
                self.active_id = self.order[0] if self.order else None
        return removed

    def rename(self, scene_id: str, label: str, *, save: bool = True) -> Scene:
        label = str(label).strip()
        if not label:
            raise ValueError("scene label cannot be empty")
        if any(item.id != scene_id and item.label == label for item in self.scenes.values()):
            raise ValueError(f"scene label already exists: {label}")
        scene = self.get(scene_id)
        scene.label = label
        scene.updated_at = _now()
        if save:
            self.save()
        return scene

    def reorder(self, order: Iterable[str], *, save: bool = True) -> list[str]:
        order = [str(item) for item in order]
        if set(order) != set(self.scenes) or len(order) != len(self.scenes):
            raise ValueError("scene order must be a permutation of existing scene ids")
        self.order = order
        if self.active_id not in self.scenes:
            self.active_id = self.order[0] if self.order else None
        if save:
            self.save()
        return list(self.order)

    def duplicate(self, scene_id: str, *, label: str | None = None, save: bool = True) -> Scene:
        source = self.get(scene_id)
        scene = self.add(
            label=label or self._unique_label(f"{source.label} copy"),
            structure_name=source.structure_name,
            state_patch=source.state_patch,
            camera=source.camera,
            save=False,
        )
        source_idx = self.order.index(source.id)
        self.order.remove(scene.id)
        self.order.insert(source_idx + 1, scene.id)
        self.active_id = scene.id
        if save:
            self.save()
        return scene

    def set_active(self, scene_id: str, *, save: bool = True) -> Scene:
        scene = self.get(scene_id)
        self.active_id = scene.id
        if save:
            self.save()
        return scene

    def patch_scene(self, scene_id: str, patch: dict[str, Any], *, save: bool = True) -> Scene:
        scene = self.get(scene_id)
        scene.patch(patch)
        if save:
            self.save()
        return scene

    def _unique_label(self, base: str) -> str:
        base = str(base).strip() or "Scene"
        labels = {scene.label for scene in self.scenes.values()}
        if base not in labels:
            return base
        idx = 2
        while f"{base} {idx}" in labels:
            idx += 1
        return f"{base} {idx}"
