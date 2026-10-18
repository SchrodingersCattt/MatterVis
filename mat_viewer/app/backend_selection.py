from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import _normalize_selection
from ..render.picking import atom_instance_id, normalize_target
from ..render.style.resolver import (
    merge_style_override,
    normalize_style_overrides,
    validate_style_override_patch,
)


def _selection_part(atom: dict[str, Any]) -> dict[str, Any]:
    part = atom.get("part") or atom.get("disorder_group") or atom.get("disorder_assembly")
    entry: dict[str, Any] = {
        "label": str(atom.get("label") or ""),
        "is_minor": bool(atom.get("is_minor")),
        "elem": str(atom.get("elem") or ""),
    }
    if part not in (None, ""):
        entry["part"] = str(part)
    return entry


def _selection_target(atom: dict[str, Any], display_index: int) -> dict[str, Any]:
    """Return the semantic target for one displayed atom.

    Coordinate picking must use the same exact identity as the Plotly
    selection trace.  Labels alone are not enough when periodic replicas or
    disorder copies share a label, so the target carries the stable instance
    id plus the legacy display metadata used by the browser inspector.
    """

    value: dict[str, Any] = {
        "kind": "atom",
        "id": atom_instance_id(atom, display_index),
        "label": str(atom.get("label") or ""),
        "element": str(atom.get("elem") or ""),
    }
    source_index = atom.get("_source_index")
    if source_index not in (None, ""):
        try:
            value["source_index"] = int(source_index)
        except (TypeError, ValueError):
            value["source_index"] = source_index
    image_shift = atom.get("_image_shift", atom.get("_pbc_image"))
    if image_shift not in (None, ""):
        try:
            value["image_shift"] = [int(item) for item in image_shift]
        except (TypeError, ValueError):
            value["image_shift"] = image_shift
    # ``normalize_target`` keeps the public target contract stable and strips
    # any accidental loader-only fields before the payload reaches JSON.
    return normalize_target(value) or {"kind": "atom", "id": value["id"]}


class _SelectionBackendMixin:
    def get_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        return copy.deepcopy(_normalize_selection(self.get_state(scene_id).get("selection")))

    def set_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        labels = [str(label) for label in atom_labels if str(label).strip()]
        selection = _normalize_selection({"atom_labels": labels, "order": labels})
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def set_target_selection(
        self,
        targets: Iterable[Any] | Any,
        scene_id: Optional[str] = None,
        *,
        additive: bool = False,
        toggle: bool = False,
    ) -> dict[str, Any]:
        """Select exact semantic targets while preserving atom-label clients.

        ``targets`` are canonical ``{kind, id}`` payloads emitted by Plotly
        interaction traces.  Atom labels are mirrored only as a compatibility
        projection for legacy selection actions; they are never used as the
        identity of a non-atom target.
        """

        if isinstance(targets, dict):
            values = [targets]
        else:
            values = list(targets or [])
        normalized = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            target = normalize_target(value)
            if target is None:
                continue
            key = (target["kind"], target["id"])
            if key not in seen:
                normalized.append(target)
                seen.add(key)
        current = self.get_selection(scene_id)
        existing = list(current.get("targets") or []) if additive or toggle else []
        by_key = {(item.get("kind"), item.get("id")): item for item in existing}
        for target in normalized:
            key = (target["kind"], target["id"])
            if toggle and key in by_key:
                by_key.pop(key, None)
            else:
                by_key[key] = target
        final_targets = list(by_key.values())
        labels = [str(item.get("label")) for item in final_targets if item.get("kind") == "atom" and item.get("label")]
        selection = _normalize_selection({
            "atom_labels": labels,
            "order": labels,
            "targets": final_targets,
            # Derive the active target from the resulting set.  In toggle
            # mode ``normalized[-1]`` may be the target that was just
            # removed; passing it to the normalizer would add it back to
            # ``targets`` as a side effect.
            "active_target": final_targets[-1] if final_targets else None,
        })
        self.patch_state({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def clear_target_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        return self.clear_selection(scene_id=scene_id)

    def set_style_override(
        self,
        target_id: str,
        kind: str,
        patch: dict[str, Any],
        scene_id: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        validate_style_override_patch(kind, patch)
        state = self.get_state(scene_id)
        overrides = merge_style_override(state.get("style_overrides"), str(target_id), str(kind), patch)
        self.patch_state({"style_overrides": overrides}, scene_id=scene_id)
        return copy.deepcopy(self.get_state(scene_id).get("style_overrides") or {})

    def reset_style_override(
        self,
        target_id: str,
        scene_id: Optional[str] = None,
        *,
        field: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        state = self.get_state(scene_id)
        overrides = normalize_style_overrides(state.get("style_overrides"))
        key = str(target_id)
        if field and key in overrides:
            overrides[key].pop(str(field), None)
            if set(overrides[key]) <= {"kind"}:
                overrides.pop(key, None)
        else:
            overrides.pop(key, None)
        self.patch_state({"style_overrides": overrides}, scene_id=scene_id)
        return copy.deepcopy(self.get_state(scene_id).get("style_overrides") or {})

    def add_to_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        current = self.get_selection(scene_id)
        order = list(current.get("order") or current.get("atom_labels") or [])
        labels = list(current.get("atom_labels") or [])
        seen = set(labels)
        for raw in atom_labels:
            label = str(raw).strip()
            if label and label not in seen:
                labels.append(label)
                order.append(label)
                seen.add(label)
        selection = _normalize_selection({"atom_labels": labels, "order": order, "active_label": order[-1] if order else None})
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def remove_from_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        remove = {str(label).strip() for label in atom_labels if str(label).strip()}
        current = self.get_selection(scene_id)
        labels = [label for label in current.get("atom_labels", []) if label not in remove]
        order = [label for label in current.get("order", []) if label in labels]
        selection = _normalize_selection({"atom_labels": labels, "order": order})
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def clear_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        # Clear exact semantic targets as well as the legacy label projection.
        # Omitting ``targets`` happened to work while the normalizer rebuilt
        # the whole selection object, but left this contract dependent on that
        # implementation detail and made atom-only outlines linger after a
        # clear event in older state payloads.
        self.patch_state(
            {
                "selection": {
                    "atom_labels": [],
                    "active_label": None,
                    "order": [],
                    "targets": [],
                    "active_target": None,
                }
            },
            scene_id=scene_id,
        )
        return self.get_selection(scene_id)

    def toggle_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        current = self.get_selection(scene_id)
        selected = set(current.get("atom_labels") or [])
        labels = [str(label).strip() for label in atom_labels if str(label).strip()]
        if all(label in selected for label in labels):
            return self.remove_from_selection(labels, scene_id=scene_id)
        return self.add_to_selection(labels, scene_id=scene_id)

    def _labels_for_scene(self, scene_id: Optional[str] = None) -> list[str]:
        scene = self.scene_for_state(self.get_state(scene_id))
        labels: list[str] = []
        seen: set[str] = set()
        for atom in scene.get("draw_atoms") or []:
            label = str(atom.get("label") or "").strip()
            if label and label not in seen:
                labels.append(label)
                seen.add(label)
        return labels

    def select_all(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        return self.set_selection(self._labels_for_scene(scene_id), scene_id=scene_id)

    def invert_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        current = set(self.get_selection(scene_id).get("atom_labels") or [])
        return self.set_selection(
            [label for label in self._labels_for_scene(scene_id) if label not in current],
            scene_id=scene_id,
        )

    def select_element(self, element: str, scene_id: Optional[str] = None) -> dict[str, Any]:
        scene = self.scene_for_state(self.get_state(scene_id))
        labels = []
        for atom in scene.get("draw_atoms") or []:
            if str(atom.get("elem") or "") == str(element):
                labels.append(str(atom.get("label") or ""))
        return self.set_selection(labels, scene_id=scene_id)

    def select_box(
        self,
        rect_pixels: Iterable[Any],
        viewport_size: Iterable[Any],
        *,
        additive: bool = False,
        scene_id: Optional[str] = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        try:
            x0, y0, x1, y1 = [float(v) for v in rect_pixels]
            width, height = [max(1.0, float(v)) for v in viewport_size]
        except (TypeError, ValueError):
            return self.get_selection(scene_id)
        lo_x, hi_x = sorted((x0, x1))
        lo_y, hi_y = sorted((y0, y1))
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        atoms = scene.get("draw_atoms") or []
        if lo_x <= 0.0 and lo_y <= 0.0 and hi_x >= width and hi_y >= height:
            # Keep the fast full-cell path, but include exact atom targets so
            # this API has the same semantic contract as a small coordinate
            # pick.  ``select_all`` intentionally remains label-only for its
            # legacy keyboard/API callers.
            parts = [_selection_part(atom) for atom in atoms]
            picked = [part["label"] for part in parts if part["label"]]
            targets = [_selection_target(atom, index) for index, atom in enumerate(atoms)]
            selection = _normalize_selection(
                {
                    "atom_labels": picked,
                    "order": picked,
                    "parts": parts,
                    "targets": targets,
                    "active_target": targets[-1] if targets else None,
                }
            )
            if additive:
                current = self.get_selection(scene_id)
                merged_labels = list(current.get("atom_labels") or [])
                merged_parts = list(current.get("parts") or [])
                for part in parts:
                    label = part["label"]
                    if label and label not in merged_labels:
                        merged_labels.append(label)
                        merged_parts.append(part)
                by_key: dict[tuple[str, str], dict[str, Any]] = {}
                for target in list(current.get("targets") or []) + targets:
                    normalized = normalize_target(target)
                    if normalized is not None:
                        by_key[(normalized["kind"], normalized["id"])] = normalized
                merged_targets = list(by_key.values())
                selection = _normalize_selection(
                    {
                        "atom_labels": merged_labels,
                        "order": merged_labels,
                        "parts": merged_parts,
                        "targets": merged_targets,
                        "active_target": targets[-1] if targets else current.get("active_target"),
                    }
                )
            if not commit:
                return selection
            self.commit_patch({"selection": selection}, scene_id=scene_id)
            return self.get_selection(scene_id)
        if not atoms:
            return self.get_selection(scene_id)
        coords = np.asarray([atom.get("cart", [0.0, 0.0, 0.0]) for atom in atoms], dtype=float)
        try:
            from mat_viewer.render.screen_projection import project_gl3d_pixels
            from mat_viewer.render.viewport import _plotly_camera_from_scene
            from mat_viewer.render.viewport_spec import resolve_viewport

            style = self.style_for_state(state, scene)
            viewport = resolve_viewport(scene, style)
            # Stored cameras are often position/focal. Projecting those
            # with the default eye picks the wrong atom.
            camera = _plotly_camera_from_scene(scene, style)
            projection = state.get("projection")
            xy = project_gl3d_pixels(
                coords,
                camera=camera,
                ranges=viewport.ranges,
                aspectratio=viewport.aspectratio,
                width=width,
                height=height,
                projection=str(projection) if projection else None,
            )
        except Exception:
            return self.get_selection(scene_id)
        center_x = 0.5 * (lo_x + hi_x)
        center_y = 0.5 * (lo_y + hi_y)
        hits: list[tuple[float, int, dict[str, Any]]] = []
        for index, (atom, sx, sy) in enumerate(zip(atoms, xy[:, 0], xy[:, 1])):
            if not (np.isfinite(sx) and np.isfinite(sy)):
                continue
            if lo_x <= float(sx) <= hi_x and lo_y <= float(sy) <= hi_y:
                dist = (float(sx) - center_x) ** 2 + (float(sy) - center_y) ** 2
                hits.append((dist, index, atom))
        click = (hi_x - lo_x) <= 48.0 and (hi_y - lo_y) <= 48.0
        if click and hits:
            hits = [min(hits, key=lambda item: item[0])]
        parts = [_selection_part(atom) for _dist, _index, atom in hits]
        picked = [part["label"] for part in parts if part["label"]]
        targets = [_selection_target(atom, index) for _dist, index, atom in hits]
        selection = _normalize_selection(
            {
                "atom_labels": picked,
                "order": picked,
                "parts": parts,
                "targets": targets,
                "active_target": targets[-1] if targets else None,
            }
        )
        if additive:
            current = self.get_selection(scene_id)
            merged_labels = list(current.get("atom_labels") or [])
            merged_parts = list(current.get("parts") or [])
            seen = set(merged_labels)
            for part in parts:
                label = part["label"]
                if label and label not in seen:
                    merged_labels.append(label)
                    merged_parts.append(part)
                    seen.add(label)
            by_key: dict[tuple[str, str], dict[str, Any]] = {}
            for target in list(current.get("targets") or []) + targets:
                normalized = normalize_target(target)
                if normalized is not None:
                    by_key[(normalized["kind"], normalized["id"])] = normalized
            merged_targets = list(by_key.values())
            selection = _normalize_selection(
                {
                    "atom_labels": merged_labels,
                    "order": merged_labels,
                    "parts": merged_parts,
                    "targets": merged_targets,
                    "active_target": targets[-1] if targets else current.get("active_target"),
                }
            )
        if not commit:
            return selection
        self.commit_patch({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def select_fragment(self, fragment_label: str, scene_id: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        fragment_labels = scene.get("atom_fragment_labels") or []
        labels = [
            str(atom.get("label") or "")
            for idx, atom in enumerate(scene.get("draw_atoms") or [])
            if idx < len(fragment_labels) and str(fragment_labels[idx]) == str(fragment_label)
        ]
        return self.set_selection(labels, scene_id=scene_id)

    def promote_selection_to_atom_group(
        self,
        name: str | None = None,
        color: str | None = None,
        scene_id: Optional[str] = None,
    ) -> str | None:
        labels = self.get_selection(scene_id).get("atom_labels") or []
        if not labels:
            return None
        group = self.add_atom_group(
            selector={"labels": labels},
            name=name or "selection",
            color=color or "#FFD24A",
            visible=True,
            scene_id=scene_id,
        )
        self.clear_selection(scene_id)
        return group.get("id")

    def hide_selection(self, scene_id: Optional[str] = None) -> str | None:
        labels = self.get_selection(scene_id).get("atom_labels") or []
        if not labels:
            return None
        group = self.add_atom_group(
            selector={"labels": labels},
            name="hide selection",
            color="#888888",
            visible=False,
            scene_id=scene_id,
        )
        self.clear_selection(scene_id)
        return group.get("id")

    def focus_selection_camera(self, scene_id: Optional[str] = None) -> dict[str, Any] | None:
        selected = set(self.get_selection(scene_id).get("atom_labels") or [])
        if not selected:
            return None
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        coords = [
            np.asarray(atom.get("cart"), dtype=float)
            for atom in scene.get("draw_atoms") or []
            if str(atom.get("label") or "") in selected
        ]
        if not coords:
            return None
        center = np.asarray(coords, dtype=float).mean(axis=0)
        camera = self.get_camera(scene_id)
        camera["center"] = {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])}
        return self.set_camera(camera, scene_id=scene_id)
