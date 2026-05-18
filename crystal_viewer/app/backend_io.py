from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *
from .camera_helpers import *
from .style_helpers import *
from .runtime import _prewarm_bundle_async


class _IOBackendMixin:
    def add_uploaded_bundle(self, contents: str, filename: str) -> LoadedCrystal:
        # Charge the three legs (decode + parse via gemmi, register
        # bundle, create scene) separately so the perf log makes the
        # actual bottleneck obvious. Empirically the ``load_uploaded_cif``
        # call dominates for non-trivial structures (CIF parsing +
        # symmetry expansion + bond perception).
        with perf_log.time_block(
            "upload:load_uploaded_cif",
            kind="event",
            filename=filename,
            data_url_bytes=len(contents or ""),
        ):
            bundle = load_uploaded_cif(
                contents=contents,
                filename=filename,
                existing_names=self.structure_names,
                preset=self.preset,
            )
        with perf_log.time_block("upload:create_scene", kind="event", structure=bundle.name):
            with self._lock:
                self._drop_placeholder()
                self.bundles[bundle.name] = bundle
                self.structure_names.append(bundle.name)
                self.create_scene(structure=bundle.name, label=bundle.name)
            _prewarm_bundle_async(self, bundle.name)
        return bundle

    def add_uploaded_file_bytes(self, data: bytes, filename: str) -> LoadedCrystal:
        # Sanitise the user-supplied filename before joining it onto a
        # writable directory. ``os.path.join("/tmp/uploads", "/etc/passwd")``
        # silently drops the prefix and writes ``/etc/passwd``; even
        # without an absolute escape, ``../../foo`` walks outside the
        # upload directory. ``secure_filename`` strips both classes of
        # attack and the realpath check below is a belt-and-braces
        # second line of defence in case Werkzeug's normalisation rules
        # ever change.
        from werkzeug.utils import secure_filename

        digest = hashlib.sha256(data).hexdigest()
        existing_record = (self.upload_manifest.get("uploads") or {}).get(digest)
        if isinstance(existing_record, dict):
            existing_name = existing_record.get("name")
            if existing_name in self.structure_names:
                bundle = self.get_bundle(existing_name)
                setattr(bundle, "_upload_existing", True)
                return bundle

        upload_dir = os.path.realpath(os.path.join(tempfile.gettempdir(), "crystal_viewer_uploads"))
        os.makedirs(upload_dir, exist_ok=True)
        raw_basename = os.path.basename(filename or "")
        safe = secure_filename(filename or "") or "upload.cif"
        leading_underscores = re.match(r"^_+", raw_basename)
        if leading_underscores and not safe.startswith("_"):
            safe = f"{leading_underscores.group(0)}{safe}"
        if not safe.lower().endswith(".cif"):
            safe = f"{safe}.cif"
        # Persist by content hash as well as display filename. Different
        # uploads often share a simple name like ``DP.cif``; writing the raw
        # filename would let a later upload overwrite the CIF backing an
        # existing manifest record and corrupt restored scenes after restart.
        storage_name = f"{digest[:16]}_{safe}"
        path = os.path.realpath(os.path.join(upload_dir, storage_name))
        if os.path.commonpath([path, upload_dir]) != upload_dir:
            raise ValueError(f"unsafe upload filename: {filename!r}")
        with perf_log.time_block(
            "upload:write_temp_file",
            kind="event",
            filename=safe,
            bytes=len(data),
        ):
            with open(path, "wb") as handle:
                handle.write(data)
        stem = os.path.splitext(safe)[0]
        safe_name = stem
        suffix = 2
        while safe_name in self.structure_names:
            safe_name = f"{stem}_{suffix}"
            suffix += 1
        # ``build_loaded_crystal`` parses the CIF (gemmi), expands
        # symmetry, builds bonds and -- if the preset asks for it --
        # runs molcryskit topology analysis. For a 1.6 MB CIF this is
        # by far the slowest leg of the upload (~15 s). Charging it
        # separately makes the bottleneck unambiguous in the log.
        with perf_log.time_block(
            "upload:build_loaded_crystal",
            kind="event",
            structure=safe_name,
            cif_path=path,
        ):
            bundle = build_loaded_crystal(name=safe_name, cif_path=path, title=stem, preset=self.preset, source="upload")
        with perf_log.time_block("upload:create_scene", kind="event", structure=bundle.name):
            with self._lock:
                self._drop_placeholder()
                self.bundles[bundle.name] = bundle
                self.structure_names.append(bundle.name)
                self.create_scene(structure=bundle.name, label=bundle.name)
            _prewarm_bundle_async(self, bundle.name)
        self.upload_manifest.setdefault("uploads", {})[digest] = {
            "name": bundle.name,
            "path": path,
            "sha256": digest,
            "original_filename": filename,
            "title": stem,
        }
        try:
            self._save_upload_manifest()
        except OSError as exc:  # pragma: no cover - read-only / disk-full
            print(f"[crystal_viewer] could not persist upload manifest: {exc}", file=sys.stderr)
        setattr(bundle, "_upload_existing", False)
        return bundle


    def _safe_preset_path(self, path: Optional[str], *, allow_external: bool = False) -> Optional[str]:
        """Resolve ``path`` against ``<root>/.local`` and reject anything
        that escapes that directory.

        The REST handlers expose ``/api/v{1,2}/preset/save`` and
        ``/preset/load`` with a client-controlled ``path`` field. Without
        this guard, any caller able to reach the API has an
        arbitrary-file-write (and an arbitrary-JSON-read) primitive on
        the host. Restricting to ``<root>/.local`` keeps the caller-
        facing contract (``path`` still works) while collapsing the
        attack surface to a single state directory the app already
        owns. ``path=None`` falls through to the default location.
        """
        if path is None:
            return None
        if allow_external:
            return os.path.realpath(path)
        safe_root = os.path.realpath(os.path.join(self.root_dir, LOCAL_STATE_DIRNAME))
        os.makedirs(safe_root, exist_ok=True)
        candidate = path if os.path.isabs(path) else os.path.join(safe_root, path)
        resolved = os.path.realpath(candidate)
        if os.path.commonpath([resolved, safe_root]) != safe_root:
            raise ValueError(
                f"preset path must resolve inside {safe_root!r}, got {path!r}"
            )
        return resolved

    def save_preset(self, path: Optional[str] = None, *, allow_external: bool = False) -> dict[str, Any]:
        target = self._safe_preset_path(path, allow_external=allow_external) or self.preset_path
        state = self.get_state()
        bundle = self.get_bundle(state["structure"])
        scene = self.scene_for_state(state)
        preset_data = load_preset(target) if os.path.exists(target) else default_preset()
        preset_data["version"] = max(int(preset_data.get("version", 1) or 1), 2)
        preset_data["style"].update(self.style_for_state(state))
        preset_data.setdefault("structures", {})
        preset_data["structures"][bundle.name] = {
            "camera": state.get("camera") or scene.get("camera"),
            "show_hydrogen": self.show_hydrogen_for_state(state),
            "style": self.style_for_state(state),
        }
        preset_data["scenes"] = [item for item in self.scene_store.list()]
        preset_data["active_id"] = self.scene_store.active_id
        preset_data["order"] = list(self.scene_store.order)
        save_preset(target, preset_data)
        self.preset = preset_data
        return {"path": target, "structure": bundle.name, "scenes": len(preset_data["scenes"])}

    def load_preset_from_path(self, path: Optional[str], *, allow_external: bool = False) -> dict[str, Any]:
        if not path:
            raise ValueError("path is required")
        target = self._safe_preset_path(path, allow_external=allow_external)
        self.preset = load_preset(target)
        self.preset_path = target
        if isinstance(self.preset.get("scenes"), list):
            store = SceneStore(self.scene_store.path)
            for item in self.preset.get("scenes") or []:
                try:
                    scene = Scene.from_dict(item)
                except Exception:
                    continue
                if scene.structure_name not in self.structure_names:
                    continue
                if scene.id in store.scenes:
                    continue
                store.scenes[scene.id] = scene
                store.order.append(scene.id)
            order = [str(item) for item in (self.preset.get("order") or [])]
            if order and set(order) == set(store.scenes):
                store.order = order
            active_id = self.preset.get("active_id")
            store.active_id = str(active_id) if active_id in store.scenes else (store.order[0] if store.order else None)
            if store.scenes:
                self.scene_store = store
                self.scene_store.save()
        for bundle in self.bundles.values():
            bundle.scene_cache.clear()
            cache = getattr(bundle, "_topology_state_cache", None)
            if cache:
                cache.clear()
        # The new preset can rewrite per-scene style + transforms, so
        # any cached figures from the previous preset are stale even
        # when the state-dict keys happen to collide.
        self._invalidate_figure_cache()
        structure = self.get_state()["structure"]
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
            self.pending_state = copy.deepcopy(self.current_state)
            self._bump_version()
        else:
            self.patch_state(self.default_state(structure))
        return {"path": target, "state": self.get_state()}

    def export_static(self, output_path: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state()
        if state.get("structure") == PLACEHOLDER_STRUCTURE:
            return {
                "returncode": 1,
                "stdout": "",
                "stderr": "No structure is loaded yet. Upload or preload a CIF before exporting.",
            }
        self.save_preset()
        cmd = [
            os.environ.get("PYTHON", "python"),
            "-m",
            LEGACY_EXPORT_MODULE,
            "--preset",
            self.preset_path,
            "--both",
        ]
        proc = subprocess.run(cmd, cwd=self.root_dir, capture_output=True, text=True)
        payload = {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
        if output_path:
            payload["output_path"] = output_path
        return payload

    def websocket_snapshot(self, *, include_figure: bool = False) -> dict[str, Any]:
        state = self.get_state()
        snapshot = {
            "version": self.version,
            "state": state,
            "structures": self.list_structures(),
        }
        if include_figure:
            latest = self.latest_figure_broadcast()
            if (
                latest
                and latest.get("scene_id") == state.get("scene_id")
                and self._figure_payload_has_scene3d(latest.get("figure"))
                and self._figure_state_matches_current(latest.get("scene_id"), latest.get("state"))
            ):
                snapshot["figure"] = latest["figure"]
                snapshot["figure_version"] = latest.get("figure_version", self.version)
                snapshot["figure_seq"] = latest.get("figure_seq")
                snapshot["scene_id"] = latest.get("scene_id")
        return snapshot


