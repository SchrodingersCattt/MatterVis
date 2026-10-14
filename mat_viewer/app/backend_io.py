from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *
from .camera_helpers import *
from .style_helpers import *
from .runtime import _prewarm_bundle_async
from ..loader.cif_export import loaded_crystal_to_cif


class _IOBackendMixin:
    def upload_status(self, job_id: str) -> dict[str, Any]:
        """Return the public status for one asynchronous CIF upload.

        Upload parsing happens outside the request thread.  The old endpoint
        returned a pending placeholder but had no completion/error channel, so
        a MolCrysKit parser error left the browser's ``Processing`` message in
        place forever.  Keep the internal path/exception details private and
        expose only the stable job id, scene name, and a human-readable error.
        """
        key = str(job_id or "")
        with self._lock:
            record = self._upload_jobs.get(key)
            if record is None:
                raise KeyError(key)
            payload = {
                "job_id": key,
                "name": record.get("name"),
                "status": record.get("status", "pending"),
            }
            for field in ("scene_id", "error"):
                value = record.get(field)
                if value:
                    payload[field] = str(value)
            return payload

    def _set_upload_status_locked(
        self,
        job_id: str,
        *,
        status: str,
        scene_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update a job record while ``self._lock`` is held."""
        record = self._upload_jobs.setdefault(str(job_id), {"job_id": str(job_id)})
        record["status"] = str(status)
        if scene_id:
            record["scene_id"] = str(scene_id)
        if error:
            record["error"] = str(error)
        elif status != "error":
            record.pop("error", None)

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
                self.bundles[bundle.name] = bundle
                self.structure_names.append(bundle.name)
                self._drop_placeholder()
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

        # The file path is content-addressed, so concurrent requests for the
        # same bytes may safely write it before taking the backend lock.  The
        # lock below then makes digest/name reservation atomic and prevents two
        # requests from creating a ``foo_2`` tab for one upload.

        upload_dir = os.path.realpath(os.path.join(tempfile.gettempdir(), "mat_viewer_uploads"))
        os.makedirs(upload_dir, exist_ok=True)
        raw_basename = os.path.basename(filename or "")
        safe = secure_filename(filename or "") or "upload.cif"
        leading_underscores = re.match(r"^_+", raw_basename)
        if leading_underscores and not safe.startswith("_"):
            safe = f"{leading_underscores.group(0)}{safe}"
        if not safe.lower().endswith(".cif"):
            safe = f"{safe}.cif"
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
            # Write to a per-request temporary inode and atomically replace
            # the content-addressed target.  Two concurrent retries can then
            # never expose a truncated CIF to MolCrysKit while one request is
            # still writing the shared path.
            fd, temporary_path = tempfile.mkstemp(
                prefix=f".{digest[:12]}-", suffix=".tmp", dir=upload_dir
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                os.replace(temporary_path, path)
            finally:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
        stem = os.path.splitext(safe)[0]

        # ── Async upload: defer build_loaded_crystal to background ──
        # Check whether the caller wants the legacy synchronous path
        # (used by tests that assert the bundle is ready before the call
        # returns). Default is async for real-time responsiveness.
        sync_mode = bool(getattr(self, "_upload_sync_mode", False))
        wait_for_sync: tuple[threading.Event, str, str] | None = None

        with self._lock:
            existing_record = (self.upload_manifest.get("uploads") or {}).get(digest)
            reservation = self._upload_reservations.get(digest)
            if reservation:
                existing_name = str(reservation.get("name") or "")
                bundle = self.bundles.get(existing_name)
                if bundle is not None:
                    setattr(bundle, "_upload_existing", True)
                    target_scene_id = next(
                        (
                            scene_id
                            for scene_id, scene in self.scene_store.scenes.items()
                            if scene.structure_name == existing_name
                        ),
                        None,
                    )
                    if target_scene_id:
                        self.set_active_scene(target_scene_id, broadcast=True)
                    return bundle
                completion = self._upload_completion_events.get(digest)
                if completion is not None:
                    wait_for_sync = (
                        completion,
                        existing_name,
                        str(reservation.get("job_id") or f"upload_{digest[:12]}"),
                    )
            if wait_for_sync is None and isinstance(existing_record, dict):
                existing_name = str(existing_record.get("name") or "")
                # A request already being parsed in this process is simply an
                # idempotent retry.  Return its placeholder and let the one
                # worker publish the result to all callers.
                reservation = self._upload_reservations.get(digest)
                if reservation and existing_name in self.structure_names:
                    bundle = self.bundles.get(existing_name)
                    if bundle is not None:
                        setattr(bundle, "_upload_existing", True)
                        target_scene_id = next(
                            (
                                scene_id
                                for scene_id, scene in self.scene_store.scenes.items()
                                if scene.structure_name == existing_name
                            ),
                            None,
                        )
                        if target_scene_id:
                            self.set_active_scene(target_scene_id, broadcast=True)
                        return bundle

                # Failed jobs are retryable.  Remove their stale placeholder
                # and manifest record before reserving the same content again.
                if (
                    str(existing_record.get("status") or "") == "error"
                    and existing_name
                ):
                    self._remove_failed_upload_locked(existing_name, digest)
                    existing_record = None
                elif existing_name in self.structure_names:
                    try:
                        bundle = self.get_bundle(existing_name)
                    except Exception:
                        # Keep the original parser exception observable through
                        # the normal request error path; a bad persisted upload
                        # must not be silently treated as a fresh name.
                        raise
                    setattr(bundle, "_upload_existing", True)
                    target_scene_id = next(
                        (
                            scene_id
                            for scene_id, scene in self.scene_store.scenes.items()
                            if scene.structure_name == existing_name
                        ),
                        None,
                    )
                    if target_scene_id:
                        self.set_active_scene(target_scene_id, broadcast=True)
                    else:
                        self.create_scene(structure=existing_name, label=existing_name)
                    return bundle

            if wait_for_sync is None:
                safe_name = stem
                suffix = 2
                while (
                    safe_name in self.structure_names
                    or safe_name in self._upload_reserved_names
                ):
                    safe_name = f"{stem}_{suffix}"
                    suffix += 1

                job_id = f"upload_{digest[:12]}"
                self._upload_reserved_names.add(safe_name)
                if sync_mode:
                    self._upload_completion_events[digest] = threading.Event()
                    self._upload_reservations[digest] = {
                        "name": safe_name,
                        "job_id": job_id,
                    }
                    self._upload_jobs[job_id] = {
                        "job_id": job_id,
                        "name": safe_name,
                        "status": "pending",
                    }

        if wait_for_sync is not None:
            completion, existing_name, existing_job_id = wait_for_sync
            completion.wait()
            with self._lock:
                bundle = self.bundles.get(existing_name)
                error = self._upload_jobs.get(existing_job_id, {}).get("error")
                if bundle is None:
                    raise RuntimeError(error or f"upload {existing_job_id} did not complete")
                setattr(bundle, "_upload_existing", True)
            return bundle

        if sync_mode:
            try:
                return self._upload_sync(safe_name, path, stem, filename, digest)
            except Exception:
                with self._lock:
                    self._upload_reserved_names.discard(safe_name)
                    self._set_upload_status_locked(
                        job_id,
                        status="error",
                        error="upload failed",
                    )
                raise
            finally:
                with self._lock:
                    self._upload_reservations.pop(digest, None)
                    completion = self._upload_completion_events.pop(digest, None)
                    if completion is not None:
                        completion.set()
                    self._upload_reserved_names.discard(safe_name)
                    if self._upload_jobs.get(job_id, {}).get("status") == "pending":
                        self._set_upload_status_locked(job_id, status="ready")

        # Register before creating the scene: default_state looks up its bundle.
        # Also register before submitting work so a fast worker cannot be
        # overwritten by the pending placeholder after it finishes.
        placeholder = build_empty_bundle(name=safe_name, title=stem)
        placeholder.source = "upload"
        placeholder.cif_path = path
        setattr(placeholder, "_upload_existing", False)
        setattr(placeholder, "_upload_pending", True)
        setattr(placeholder, "_upload_job_id", job_id)

        # Create placeholder scene tab immediately so the UI shows
        # something while the background job runs. The scene_store
        # keeps a pending entry until the bundle is ready.
        with self._lock:
            # Reserve the name so concurrent uploads don't collide.
            self.bundles[safe_name] = placeholder
            self.structure_names.append(safe_name)
            self._drop_placeholder()
            scene_payload = self.create_scene(structure=safe_name, label=safe_name)
            scene_id = scene_payload.get("id") if isinstance(scene_payload, dict) else None
            self._upload_reservations[digest] = {
                "name": safe_name,
                "job_id": job_id,
            }
            self._upload_jobs[job_id] = {
                "job_id": job_id,
                "name": safe_name,
                "scene_id": scene_id,
                "status": "pending",
            }
            self._upload_reserved_names.discard(safe_name)

        # Persist manifest early (idempotent protection for restarts).
        with self._lock:
            self.upload_manifest.setdefault("uploads", {})[digest] = {
                "name": safe_name,
                "path": path,
                "sha256": digest,
                "original_filename": filename,
                "title": stem,
                "status": "pending",
            }
            try:
                self._save_upload_manifest()
            except OSError:
                pass

        # Submit only after the pending bundle and scene are registered.
        self._submit_load_job(job_id, safe_name, path, stem, filename, digest)

        return placeholder

    def _remove_failed_upload_locked(
        self,
        name: str,
        digest: str,
        *,
        remove_manifest: bool = True,
    ) -> None:
        """Drop a failed placeholder so the same CIF can be retried.

        The worker keeps an ``error`` manifest record for diagnostics.  A
        subsequent retry calls this helper with the default and removes that
        stale record before reserving the new job.
        """
        self._upload_reservations.pop(digest, None)
        if remove_manifest:
            record = self.upload_manifest.get("uploads") or {}
            record.pop(digest, None)
        self.bundles.pop(name, None)
        self.structure_names = [item for item in self.structure_names if item != name]
        for scene_id, scene in list(self.scene_store.scenes.items()):
            if scene.structure_name == name:
                self.scene_store.remove(scene_id, save=False)
                self._render_revisions.pop(str(scene_id), None)
        if not self.structure_names:
            placeholder = build_empty_bundle(name=PLACEHOLDER_STRUCTURE)
            self.bundles[placeholder.name] = placeholder
            self.structure_names = [placeholder.name]
            self.create_scene(structure=placeholder.name, label=placeholder.name)
        if self.scene_store.active_id:
            self.current_state = self.scene_state(self.scene_store.active_id)
            self.pending_state = self._state_snapshot(
                self.current_state, self.scene_store.active_id
            )
        self._bump_version()
        self._request_scene_store_save()

    def _upload_sync(self, safe_name: str, path: str, stem: str, filename: str, digest: str) -> LoadedCrystal:
        """Legacy synchronous upload path used by tests and --cif CLI."""
        with perf_log.time_block(
            "upload:build_loaded_crystal",
            kind="event",
            structure=safe_name,
            cif_path=path,
        ):
            bundle = build_loaded_crystal(name=safe_name, cif_path=path, title=stem, preset=self.preset, source="upload")
        with perf_log.time_block("upload:create_scene", kind="event", structure=bundle.name):
            with self._lock:
                self.bundles[bundle.name] = bundle
                if bundle.name not in self.structure_names:
                    self.structure_names.append(bundle.name)
                self._drop_placeholder()
                self.create_scene(structure=bundle.name, label=bundle.name)
                self._upload_reserved_names.discard(bundle.name)
            _prewarm_bundle_async(self, bundle.name)
        with self._lock:
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
                print(f"[mat_viewer] could not persist upload manifest: {exc}", file=sys.stderr)
        setattr(bundle, "_upload_existing", False)
        return bundle

    def _submit_load_job(self, job_id: str, safe_name: str, path: str, stem: str, filename: str, digest: str) -> None:
        """Submit the heavy CIF parse + scene build to a background thread."""
        load_executor = getattr(self, "_load_executor", None)
        if load_executor is None:
            # Lazy-create; keep max_workers=1 so uploads are serialised
            # and don't compete with the topology/render pools.
            from concurrent.futures import ThreadPoolExecutor
            self._load_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mattervis-upload")
            load_executor = self._load_executor

        def _job():
            try:
                with perf_log.time_block(
                    "upload:build_loaded_crystal",
                    kind="event",
                    structure=safe_name,
                    cif_path=path,
                ):
                    bundle = build_loaded_crystal(name=safe_name, cif_path=path, title=stem, preset=self.preset, source="upload")
                render_state = None
                with perf_log.time_block("upload:register_bundle", kind="event", structure=bundle.name):
                    with self._lock:
                        self.bundles[bundle.name] = bundle
                        self._upload_reservations.pop(digest, None)
                        self._set_upload_status_locked(
                            job_id,
                            status="ready",
                            scene_id=next(
                                (
                                    scene_id
                                    for scene_id, scene in self.scene_store.scenes.items()
                                    if scene.structure_name == bundle.name
                                ),
                                None,
                            ),
                        )
                        # The placeholder and loaded bundle share the same
                        # scene/state cache key. Drop the placeholder figure
                        # before scheduling the replacement render.
                        self._invalidate_figure_cache()
                    # Patch the pending manifest entry.
                    with self._lock:
                        record = (self.upload_manifest.get("uploads") or {}).get(digest)
                        if isinstance(record, dict):
                            record.pop("status", None)
                            record.pop("error", None)
                        try:
                            self._save_upload_manifest()
                        except OSError:
                            pass
                        active_scene_id = self.scene_store.active_id
                        self._bump_version()
                    # ``get_state`` acquires ``self._lock``.  Never call it
                    # while holding that non-reentrant lock: doing so left
                    # every successful upload's placeholder in place and made
                    # the browser appear to load an empty scene forever.
                    try:
                        state = self.get_state(active_scene_id)
                    except KeyError:
                        # A user may close the pending tab while MolCrysKit
                        # is still parsing.  The upload itself succeeded; use
                        # whichever scene is active now instead of turning a
                        # harmless tab close into a failed job.
                        state = self.get_state()
                    with self._lock:
                        self.pending_state = copy.deepcopy(state)
                    render_state = state if state.get("structure") == bundle.name else None
                # The placeholder scene may already have produced an empty
                # figure. Once the CIF is ready, explicitly request a full
                # render for the active uploaded scene so reopening the tab
                # cannot leave the browser with axes and no atoms.
                if render_state is not None:
                    self._render_worker.request_figure_build(render_state)
                _prewarm_bundle_async(self, bundle.name)
            except Exception as exc:
                print(f"[mat_viewer] background upload failed for {safe_name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                message = f"{type(exc).__name__}: {exc}"
                with self._lock:
                    # Mark the job before dropping the placeholder so a
                    # browser polling the status endpoint receives the real
                    # parser error even if the scene disappears immediately.
                    self._set_upload_status_locked(job_id, status="error", error=message)
                    record = (self.upload_manifest.get("uploads") or {}).get(digest)
                    if isinstance(record, dict):
                        record["status"] = "error"
                        record["error"] = message
                        try:
                            self._save_upload_manifest()
                        except OSError:
                            pass
                    self._remove_failed_upload_locked(
                        safe_name, digest, remove_manifest=False
                    )

        load_executor.submit(_job)


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
        try:
            inside_safe_root = os.path.commonpath([resolved, safe_root]) == safe_root
        except ValueError:
            # Windows rejects commonpath across drives. Such a path is
            # necessarily outside the app-local preset directory.
            inside_safe_root = False
        if not inside_safe_root:
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
                self._render_revisions = {
                    str(scene_id): 0 for scene_id in self.scene_store.scenes
                }
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
            self.pending_state = self._state_snapshot(self.current_state, self.scene_store.active_id)
            self._bump_version()
        else:
            self.patch_state(self.default_state(structure))
        return {"path": target, "state": self.get_state()}

    @staticmethod
    def _cif_download_filename(value: Any) -> str:
        """Return a safe, stable filename for browser and API downloads."""

        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "mattervis").strip())
        stem = stem.strip("._") or "mattervis"
        if stem.lower().endswith(".cif"):
            stem = stem[:-4]
        return f"{stem}.cif"

    def current_cif(self, scene_id: Optional[str] = None) -> str:
        """Serialize the loaded source structure for ``scene_id`` as CIF.

        The active display mode and display transforms intentionally do not
        alter the exported source.  They are view state; the canonical
        ``LoadedCrystal.crystal`` remains the structure users uploaded or
        opened and is the object MolCrysKit serializes.
        """

        state = self.get_state(scene_id)
        structure = state.get("structure")
        if structure in (None, PLACEHOLDER_STRUCTURE):
            raise ValueError("No structure is loaded yet. Upload or preload a CIF before exporting.")
        bundle = self.get_bundle(str(structure))
        if getattr(bundle, "_upload_pending", False):
            raise RuntimeError("The structure is still loading; try exporting again when processing finishes.")
        return loaded_crystal_to_cif(bundle)

    def export_cif(
        self,
        output_path: Optional[str] = None,
        *,
        scene_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Export the current source structure as CIF.

        Without ``output_path`` this returns the download payload used by the
        web/API layers.  Supplying a path additionally writes the exact text
        to disk, which keeps headless callers on the same serialization path.
        """

        state = self.get_state(scene_id)
        content = self.current_cif(scene_id)
        bundle = self.get_bundle(str(state["structure"]))
        filename = self._cif_download_filename(state.get("scene_label") or bundle.name)
        payload: dict[str, Any] = {
            "filename": filename,
            "structure": bundle.name,
            "scene_id": state.get("scene_id"),
            "content": content,
        }
        if output_path:
            target = os.path.realpath(os.path.expanduser(str(output_path)))
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            payload["path"] = target
        return payload

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
                snapshot["render_revision"] = latest.get("render_revision")
        return snapshot


