from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import _normalize_selection


class _SelectionBackendMixin:
    def get_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        return copy.deepcopy(_normalize_selection(self.get_state(scene_id).get("selection")))

    def set_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        labels = [str(label) for label in atom_labels if str(label).strip()]
        selection = _normalize_selection({"atom_labels": labels, "order": labels})
        self.patch_state({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

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
        self.patch_state({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def remove_from_selection(self, atom_labels: Iterable[Any], scene_id: Optional[str] = None) -> dict[str, Any]:
        remove = {str(label).strip() for label in atom_labels if str(label).strip()}
        current = self.get_selection(scene_id)
        labels = [label for label in current.get("atom_labels", []) if label not in remove]
        order = [label for label in current.get("order", []) if label in labels]
        selection = _normalize_selection({"atom_labels": labels, "order": order})
        self.patch_state({"selection": selection}, scene_id=scene_id)
        return self.get_selection(scene_id)

    def clear_selection(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        self.patch_state({"selection": {"atom_labels": [], "active_label": None, "order": []}}, scene_id=scene_id)
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
        camera = state.get("camera") or scene.get("camera") or self.default_camera(state)
        try:
            from crystal_viewer.math.projection import camera_screen_basis

            right, screen_up = camera_screen_basis(camera)
        except Exception:
            return self.get_selection(scene_id)
        atoms = scene.get("draw_atoms") or []
        if not atoms:
            return self.get_selection(scene_id)
        coords = np.asarray([atom.get("cart", [0.0, 0.0, 0.0]) for atom in atoms], dtype=float)
        center = coords.mean(axis=0)
        rel = coords - center[None, :]
        projected = np.stack([rel @ right, rel @ screen_up], axis=1)
        mins = projected.min(axis=0)
        maxs = projected.max(axis=0)
        span = np.maximum(maxs - mins, 1e-9)
        px = (projected[:, 0] - mins[0]) / span[0] * width
        py = height - ((projected[:, 1] - mins[1]) / span[1] * height)
        picked = [
            str(atom.get("label") or "")
            for atom, sx, sy in zip(atoms, px, py)
            if lo_x <= float(sx) <= hi_x and lo_y <= float(sy) <= hi_y
        ]
        if additive:
            return self.add_to_selection(picked, scene_id=scene_id)
        return self.set_selection(picked, scene_id=scene_id)

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
