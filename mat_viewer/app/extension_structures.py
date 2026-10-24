"""Native Web writes and explicitly targeted browser focus.

Scenes belong to the app, not an authenticated user session. A random per-page
routing token only addresses focus; it is not an authorization credential.
All Dash components/callbacks are installed before serving.
"""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from ..extensions.structures import SceneEvent, SceneHandle, StructureWriter


class WebStructureHost:
    def __init__(self, backend):
        self.backend = backend
        self.targets = {}
        self.writer = StructureWriter(self)
        self.serial = 0
        self.writer.subscribe(self._changed)

    def _changed(self, event):
        if event.kind not in ("focused", "activated"):
            self.serial += 1

    def execute(self, action):
        with self.backend._lock:
            if self.backend._closed:
                raise RuntimeError("Web host is closed")
            return action()

    def handle(self, scene_id):
        with self.backend._lock:
            if scene_id not in self.backend.scene_store.scenes:
                raise KeyError(scene_id)
            return SceneHandle(scene_id, self.backend._scene_generations[scene_id])

    def source_id(self, scene_id):
        with self.backend._lock:
            return self.backend.scene_store.get(scene_id).structure_name

    def _check(self, handle):
        if self.handle(handle.scene_id) != handle:
            raise KeyError("stale scene generation")

    def prepare(self, path: Path, name: str):
        from ..loader import build_loaded_crystal

        bundle = build_loaded_crystal(
            name=name,
            cif_path=str(path),
            preset=self.backend.preset,
            source="extension",
        )
        if not bundle.raw_atoms:
            raise ValueError("CIF must contain atoms")
        return bundle

    def commit(self, bundle, handle, label):
        backend = self.backend
        if handle:
            self._check(handle)
        # Every candidate has a unique bundle key, even when reloading a
        # duplicated scene. Never replace a sibling's shared bundle.
        scenes_before = dict(backend.scene_store.scenes)
        if handle:
            scenes_before[handle.scene_id] = deepcopy(scenes_before[handle.scene_id])
        order_before = list(backend.scene_store.order)
        active_before = backend.active_scene_id()
        current_before, pending_before = backend.current_state, backend.pending_state
        clocks = {
            name: dict(getattr(backend, name))
            for name in ("_render_revisions", "_scene_generations", "_camera_revisions")
        }
        version_before = backend.version
        backend.bundles[bundle.name] = bundle
        backend.structure_names.append(bundle.name)
        try:
            if handle is None:
                active = backend.active_scene_id()
                current, pending = backend.current_state, backend.pending_state
                scene = backend.create_scene(
                    structure=bundle.name, label=label or bundle.title
                )
                if active:
                    backend.scene_store.active_id = active
                    backend.current_state, backend.pending_state = current, pending
                return self.handle(scene["id"])
            before = backend.scene_state(handle.scene_id)
            # Preserve camera only for the exact same source coordinates/cell;
            # otherwise defaults safely fit the replacement. Always reset
            # source-ordinal selections, transforms and topology settings.
            old = backend.get_bundle(before["structure"])
            camera = (
                deepcopy(before.get("camera"))
                if not before.get("transforms")
                and before.get("display_mode")
                == backend.default_state(bundle.name).get("display_mode")
                and self._same_geometry(old, bundle)
                else None
            )
            backend.replace_scene_state_with_defaults(
                handle.scene_id, structure=bundle.name
            )
            scene = backend.scene_store.get(handle.scene_id)
            if camera is not None:
                scene.camera = camera
                scene.state_patch["camera"] = deepcopy(camera)
            # The browser rejects frames with older independent clocks. A
            # source replacement must never reset them to loader defaults.
            camera_revision = (
                max(
                    int(before.get("camera_revision", 0) or 0),
                    int(before.get("camera_version", 0) or 0),
                    backend._camera_revisions.get(handle.scene_id, 0),
                )
                + 1
            )
            scene.state_patch.update(
                geometry_version=int(before.get("geometry_version", 0) or 0) + 1,
                display_version=int(before.get("display_version", 0) or 0) + 1,
                camera_version=camera_revision,
                camera_revision=camera_revision,
                target_revision=int(before.get("target_revision", 0) or 0) + 1,
            )
            backend._camera_revisions[handle.scene_id] = camera_revision
            if backend.active_scene_id() == handle.scene_id:
                backend.current_state = backend.scene_state(handle.scene_id)
                backend.pending_state = backend._state_snapshot(
                    backend.current_state, handle.scene_id
                )
            return handle
        except BaseException:
            backend.scene_store.scenes.clear()
            backend.scene_store.scenes.update(scenes_before)
            backend.scene_store.order = order_before
            backend.scene_store.active_id = active_before
            backend.current_state, backend.pending_state = (
                current_before,
                pending_before,
            )
            backend.version = version_before
            for name, values in clocks.items():
                setattr(backend, name, values)
            backend.bundles.pop(bundle.name, None)
            backend.structure_names.remove(bundle.name)
            # ``replace_scene_state_with_defaults`` and ``create_scene`` mark
            # the scene store dirty and wake its persistence worker.  A
            # failed candidate must not leave a previously persisted,
            # partially-mutated scene on disk if that worker happened to save
            # before we reached this rollback.  Re-mark the restored snapshot
            # dirty so the corrected state is flushed as well.
            backend._request_scene_store_save()
            raise

    @staticmethod
    def _same_geometry(old, new):
        import numpy as np

        a, b = old.scene, new.scene
        aa, bb = a.get("draw_atoms", []), b.get("draw_atoms", [])
        return (
            np.array_equal(old.M, new.M)
            and len(aa) == len(bb)
            and bool(aa)
            and all(
                x.get("elem") == y.get("elem")
                and np.array_equal(x.get("cart"), y.get("cart"))
                for x, y in zip(aa, bb)
            )
        )

    def focus(self, handle, target):
        self._check(handle)
        if not isinstance(target, str) or target not in self.targets:
            raise ValueError("Web focus requires a live extension-browser-target token")
        self.targets[target]["guard"] = None
        self.targets[target]["focus"] = handle
        self.targets[target]["epoch"] += 1

    def guard_focus(self, target, guard):
        self.targets[target]["guard"] = guard

    def activated(self, scene_id, target):
        with self.backend._lock:
            if target not in self.targets or scene_id not in self.backend.scene_store.scenes:
                return
            entry = self.targets[target]
            identity = (self.handle(scene_id), self.source_id(scene_id))
            if entry.get("active") == identity:
                return
            entry["active"] = identity
            self.writer.emit(SceneEvent("activated", *identity, target=target))

    def new_target(self):
        with self.backend._lock:
            token = uuid4().hex
            self.targets[token] = {"focus": None, "serial": self.serial, "epoch": 0}
            return token

    def poll(self, target):
        with self.backend._lock:
            if not isinstance(target, str):
                return None
            entry = self.targets.get(target)
            if entry is None:
                return None
            handle = entry["focus"]
            changed = entry["serial"] != self.serial
            entry.update(focus=None, serial=self.serial)
            if handle is not None:
                try:
                    self._check(handle)
                    if entry.get("guard") is not None:
                        entry["guard"]()
                except KeyError:
                    handle = None
            if not changed and handle is None:
                return None
            # Epochs order focus requests, not the independent scene-refresh
            # notifications. A refresh can be polled between a focus poll
            # and its DOM dispatch; bumping the same epoch for that refresh
            # would make the still-valid focus event look stale. A subsequent
            # focus still advances the epoch and correctly supersedes older
            # focus events.
            if handle is not None:
                entry["epoch"] += 1
            return {
                "extension_event": True,
                "target": target,
                "epoch": entry["epoch"],
                "extension_focus": handle.scene_id if handle else None,
                "generation": handle.generation if handle else None,
                "seq": uuid4().hex,
            }

    def is_current_event(self, event):
        entry = self.targets.get(event.get("target"))
        if entry is None or entry["epoch"] != event.get("epoch"):
            return False
        if event.get("extension_focus") and entry.get("guard") is not None:
            try:
                entry["guard"]()
            except KeyError:
                return False
        return True

    def register_callbacks(self, app):
        from dash import Input, Output, State, no_update

        @app.callback(
            Output("scene-event-store", "data", allow_duplicate=True),
            Input("agent-state-poll", "n_intervals"),
            State("extension-browser-target", "data"),
            prevent_initial_call=True,
        )
        def extension_focus_poll(_tick, target):
            return self.poll(target) or no_update

        @app.callback(
            Output("extension-scene-observed", "data"),
            Input("scene-tabs", "value"),
            State("extension-browser-target", "data"),
        )
        def extension_scene_activated(scene_id, target):
            self.activated(scene_id, target)
            return no_update


def create_extension_context(backend):
    """Construct the Web context without growing the layout factory."""
    from ..extensions import ExtensionContext

    def snapshot():
        state = backend.get_state()
        return {
            "frontend": "web",
            "structure_id": state.get("structure"),
            "source_path": None,
            "scene_id": state.get("scene_id"),
            "scene_generation": state.get("scene_generation"),
            "view_revision": state.get("render_revision"),
            "selection": state.get("selection"),
        }

    host = WebStructureHost(backend)
    backend._extension_structures = host
    context = ExtensionContext("web", backend, snapshot_reader=snapshot)
    context.structures = host.writer
    from ..extensions.revisions import RevisionScenes
    context.revisions = RevisionScenes(host.writer)
    return context, host
