"""Native terminal scenes: one controller per scene, one existing canvas.

Parsing runs on StructureWriter's worker. All controller swaps, close operations
and widget updates run on Textual's event thread through call_from_thread.
"""

from pathlib import Path
from uuid import uuid4

from ..extensions.structures import SceneEvent, SceneHandle, StructureWriter


class TerminalScenes:
    def __init__(self, app):
        self.app = app
        self.active_id = "scene_" + uuid4().hex
        self.controllers = {self.active_id: app.controller}
        self.labels = {self.active_id: app.crystal.title}
        self.source_ids = {self.active_id: "source_" + uuid4().hex}
        self.writer = StructureWriter(self)
        self._publish()

    def selector(self):
        from textual.widgets import Select

        host = self

        class SceneSelector(Select):
            def on_select_changed(self, event):
                if event.value in host.controllers and event.value != host.active_id:
                    host._activate(event.value)

        widget = SceneSelector(
            [(self.labels[self.active_id], self.active_id)],
            value=self.active_id,
            allow_blank=False,
            id="native-scenes",
        )
        widget.display = False
        return widget

    def _sync_selector(self):
        from textual.widgets import Select

        widget = self.app.query_one("#native-scenes", Select)
        with widget.prevent(Select.Changed):
            widget.set_options([(self.labels[sid], sid) for sid in self.controllers])
            widget.value = self.active_id
        widget.display = len(self.controllers) > 1

    def _publish(self):
        self.app.extension_context._publish(
            scene_id=self.active_id,
            scene_generation=1,
            structure_id=self.source_ids[self.active_id],
        )

    def execute(self, action):
        if not self.app.is_running:
            raise RuntimeError("TUI writes require a mounted host")
        return self.app.call_from_thread(action)

    def handle(self, scene_id):
        if scene_id not in self.controllers:
            raise KeyError(scene_id)
        return SceneHandle(scene_id, 1)

    def _check(self, handle):
        if self.handle(handle.scene_id) != handle:
            raise KeyError("stale scene generation")

    def prepare(self, path: Path, name: str):
        from .loader_adapter import load_for_tui

        return load_for_tui(str(path), input_format="cif")

    def commit(self, crystal, handle, label):
        from dataclasses import replace

        import numpy as np

        from .controller import TerminalViewController

        if handle:
            self._check(handle)
        camera = None
        if handle:
            old = self.controllers[handle.scene_id]
            if (
                np.array_equal(old.crystal.lattice.matrix, crystal.lattice.matrix)
                and np.array_equal(old.crystal.cart_coords, crystal.cart_coords)
                and [a.element for a in old.crystal.atoms]
                == [a.element for a in crystal.atoms]
            ):
                camera = replace(old.camera)
        crystal.title = self.labels[handle.scene_id] if handle else (label or crystal.title)
        controller = TerminalViewController(crystal, camera=camera)
        scene_id = handle.scene_id if handle else "scene_" + uuid4().hex
        # Construct and render the replacement before publishing any identity.
        # A loader/controller/viewport failure leaves the old scene untouched.
        controller.resize_viewport(
            self.app.controller.width, self.app.controller.height
        )
        old_controller = self.controllers.get(scene_id)
        old_source = self.source_ids.get(scene_id)
        old_label = self.labels.get(scene_id)
        self.controllers[scene_id] = controller
        self.labels[scene_id] = old_label if handle else (label or crystal.title)
        self.source_ids[scene_id] = Path(crystal.source_path).stem
        try:
            if scene_id == self.active_id:
                self._activate(scene_id)
            self._sync_selector()
        except BaseException:
            if old_controller is None:
                self.controllers.pop(scene_id)
                self.labels.pop(scene_id)
                self.source_ids.pop(scene_id)
            else:
                self.controllers[scene_id] = old_controller
                self.labels[scene_id] = old_label
                self.source_ids[scene_id] = old_source
                if scene_id == self.active_id:
                    self.app.controller = old_controller
                    self.app.crystal = old_controller.crystal
                    self._publish()
            raise
        return self.handle(scene_id)

    def _activate(self, scene_id):
        self.active_id = scene_id
        self.app.controller = self.controllers[scene_id]
        self.app.crystal = self.app.controller.crystal
        self.app._command_selection = []
        self.app._inspector_view = None
        self._publish()
        self._sync_selector()
        self.app._apply_observation(self.app._resize_and_observe())

    def focus(self, handle, target):
        if target is not None:
            raise ValueError("TUI has one native target; omit target")
        self._check(handle)
        self._activate(handle.scene_id)

    def close_scene(self, scene_id):
        """Host-thread lifecycle hook. Keep at least one native scene alive."""
        handle = self.handle(scene_id)
        if len(self.controllers) == 1:
            raise ValueError("cannot close the last terminal scene")
        del self.controllers[scene_id]
        self.labels.pop(scene_id)
        self.source_ids.pop(scene_id)
        if scene_id == self.active_id:
            self._activate(next(iter(self.controllers)))
        self._sync_selector()
        self.writer.emit(SceneEvent("closed", handle))
