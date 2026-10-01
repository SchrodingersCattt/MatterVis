"""Tenant-scoped orchestration above the chemistry and rendering kernel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import uuid

from .auth import ApiKeyManager, AuthContext, AuthError, Authenticator
from .config import SaaSConfig
from .jobs import LocalEventBus, LocalJobRunner, RedisJobRunner
from .repository import AuthorizationError, NotFoundError, SaaSRepository
from .storage import LocalObjectStore, S3ObjectStore
from sqlalchemy import select


class SaaSService:
    def __init__(self, *, config: SaaSConfig, repository: SaaSRepository | None = None,
                 object_store: Any | None = None):
        self.config = config
        self.repository = repository or SaaSRepository(config.database_url)
        if object_store is not None:
            self.object_store = object_store
        elif config.object_store_backend == "s3":
            self.object_store = S3ObjectStore(
                bucket=config.object_store_bucket, endpoint_url=config.s3_endpoint_url,
                region_name=config.s3_region, access_key=config.s3_access_key,
                secret_key=config.s3_secret_key,
            )
        else:
            self.object_store = LocalObjectStore(config.object_store_root, config.artifact_signing_key)
        self.events = LocalEventBus()
        if config.redis_url and os.environ.get("MATTERVIS_JOB_BACKEND", "local").lower() == "redis":
            self.jobs = RedisJobRunner(self.repository, config.redis_url, self._job_event_callback)
        else:
            self.jobs = LocalJobRunner(self.repository, config.max_concurrent_jobs, self._job_event_callback)
        self.api_keys = ApiKeyManager(self.repository)
        self.authenticator = Authenticator(
            self.repository, mode=config.auth_mode,
            issuer_url=config.oidc_issuer_url, audience=config.oidc_audience,
        )

    def authenticate(self, request) -> AuthContext:
        return self.authenticator.authenticate(request)

    def close(self) -> None:
        self.jobs.shutdown(wait=False)
        self.repository.close()

    def _flush_outbox(self) -> None:
        for event in self.repository.drain_outbox():
            project_id = str(event.pop("project_id", ""))
            if project_id:
                self.events.publish(project_id, event)

    def _job_event_callback(self, _project_id: str, _event: dict) -> None:
        self._flush_outbox()

    def run_redis_worker(self) -> None:
        if not isinstance(self.jobs, RedisJobRunner):
            raise RuntimeError("MATTERVIS_JOB_BACKEND=redis and MATTERVIS_REDIS_URL are required")
        self.jobs.run_worker(self._dispatch_job)

    def _dispatch_job(self, job_id: str) -> None:
        job = self._job_by_id(job_id)
        if job.kind == "parse":
            self._parse_asset(job_id)
        else:
            self._compute_job(job_id)

    def identity(self, auth: AuthContext) -> dict[str, Any]:
        if auth.is_api_key:
            return {
                "auth_type": "api_key", "project_id": auth.project_id,
                "scopes": sorted(auth.scopes), "key_id": auth.key_id,
            }
        identity = self.repository.ensure_principal(auth.subject)
        return {"user": identity["user"], "workspace": identity["workspace"], "project": identity["project"]}

    def _project(self, auth: AuthContext, project_id: str, scope: str | None = None) -> dict[str, Any]:
        if auth.is_api_key:
            if auth.project_id != project_id:
                raise AuthorizationError("API key is scoped to another project")
            if scope and not auth.has_scope(scope):
                raise AuthorizationError(f"API key lacks scope {scope}")
            return self.repository.project_for_api_key(project_id)
        return self.repository.project_for_subject(auth.subject, project_id)

    def list_workspaces(self, auth: AuthContext) -> list[dict[str, Any]]:
        return self.repository.list_workspaces(auth.subject)

    def list_projects(self, auth: AuthContext, workspace_id: str) -> list[dict[str, Any]]:
        return self.repository.list_projects(auth.subject, workspace_id)

    def create_project(self, auth: AuthContext, workspace_id: str, name: str) -> dict[str, Any]:
        if auth.is_api_key:
            raise AuthorizationError("API keys cannot create projects")
        return self.repository.create_project(auth.subject, workspace_id, name)

    def list_assets(self, auth: AuthContext, project_id: str) -> list[dict[str, Any]]:
        self._project(auth, project_id, "structures:read")
        return self.repository.list_assets(project_id)

    def upload_asset(self, auth: AuthContext, project_id: str, filename: str, data: bytes,
                     input_format: str | None, idempotency_key: str | None) -> dict[str, Any]:
        self._project(auth, project_id, "structures:write")
        if len(data) > self.config.max_upload_bytes:
            raise ValueError(f"upload exceeds {self.config.max_upload_bytes} bytes")
        current_storage = sum(item.get("size_bytes", 0) or 0 for item in self.repository.list_assets(project_id))
        if current_storage + len(data) > self.config.max_storage_bytes:
            raise ValueError("workspace storage quota exceeded")
        digest = hashlib.sha256(data).hexdigest()
        existing = self.repository.find_asset_by_hash(project_id, digest)
        if existing is not None:
            return {"structure": existing, "existing": True, "job": None}
        asset_id = f"asset_{uuid.uuid4().hex}"
        safe_filename = str(filename).replace("\\", "_").replace("/", "_")
        storage_key = f"projects/{project_id}/structures/{asset_id}/source/{safe_filename}"
        stored = self.object_store.put_bytes(storage_key, data)
        asset = self.repository.create_asset(project_id=project_id, filename=filename,
                                             input_format=input_format, digest=stored.sha256,
                                             storage_key=storage_key, size_bytes=stored.size)
        job, duplicate = self.repository.create_job(
            project_id=project_id, kind="parse", payload={"structure_id": asset["id"]},
            idempotency_key=idempotency_key,
        )
        if not duplicate:
            self._flush_outbox()
            self.jobs.submit(job["id"], self._parse_asset)
        return {"structure": asset, "existing": False, "job": job}

    def _parse_asset(self, job_id: str) -> None:
        job = self.repository.get_job_by_id(job_id) if hasattr(self.repository, "get_job_by_id") else self._job_by_id(job_id)
        payload = json.loads(job.input_json or "{}")
        asset = self._asset_by_id(payload["structure_id"])
        source_path, temporary = self.object_store.materialize(asset.storage_key)
        metadata: dict[str, Any] = {"path": str(source_path)}
        atom_count = None
        frame_count = None
        try:
            from ..loader.structure_input import load_structure_input

            from ..loader.structure_input import count_structure_frames

            frame_count = int(count_structure_frames(str(source_path), input_format=asset.input_format))
            if frame_count > self.config.max_trajectory_frames:
                raise ValueError(
                    f"trajectory has {frame_count} frames; limit is {self.config.max_trajectory_frames}"
                )
            loaded = load_structure_input(str(source_path), input_format=asset.input_format, frame_indices=[0])
            frame = loaded.frames[0]
            bundle = getattr(frame, "bundle", None)
            atom_count = len(getattr(bundle, "raw_atoms", None) or []) if bundle is not None else None
            if atom_count is not None and atom_count > self.config.max_atoms_per_frame:
                raise ValueError(
                    f"frame has {atom_count} atoms; limit is {self.config.max_atoms_per_frame}"
                )
            metadata.update({"input_format": getattr(loaded, "input_format", asset.input_format), "frame_count": frame_count})
        except Exception as exc:
            metadata["parse_warning"] = f"{type(exc).__name__}: {exc}"
            self.repository.update_asset(asset.id, status="error", metadata=metadata)
            raise
        finally:
            if temporary:
                source_path.unlink(missing_ok=True)
        self.repository.update_asset(asset.id, status="ready", atom_count=atom_count,
                                     frame_count=frame_count, metadata=metadata)
        self._flush_outbox()

    def _job_by_id(self, job_id: str):
        with self.repository.session() as db:
            from .models import Job
            job = db.get(Job, job_id)
            if job is None:
                raise NotFoundError("job not found")
            return job

    def _asset_by_id(self, asset_id: str):
        with self.repository.session() as db:
            from .models import StructureAsset
            asset = db.get(StructureAsset, asset_id)
            if asset is None:
                raise NotFoundError("structure asset not found")
            return asset

    def list_scenes(self, auth: AuthContext, project_id: str) -> list[dict[str, Any]]:
        self._project(auth, project_id, "scenes:read")
        return self.repository.list_scenes(project_id)

    def create_scene(self, auth: AuthContext, project_id: str, structure_id: str,
                     label: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
        self._project(auth, project_id, "scenes:write")
        result = self.repository.create_scene(project_id=project_id, structure_id=structure_id,
                                             label=label, state=state)
        self._flush_outbox()
        return result

    def patch_scene(self, auth: AuthContext, project_id: str, scene_id: str,
                    patch: dict[str, Any], expected_revision: int | None) -> dict[str, Any]:
        self._project(auth, project_id, "scenes:write")
        result = self.repository.patch_scene(project_id=project_id, scene_id=scene_id,
                                             patch=patch, expected_revision=expected_revision)
        self._flush_outbox()
        return result

    def submit_job(self, auth: AuthContext, project_id: str, kind: str,
                   payload: dict[str, Any], idempotency_key: str | None) -> dict[str, Any]:
        self._project(auth, project_id, "jobs:write")
        if kind not in {"topology", "render", "export", "analysis"}:
            raise ValueError(f"unsupported job kind: {kind}")
        job, duplicate = self.repository.create_job(project_id=project_id, kind=kind,
                                                     payload=payload, idempotency_key=idempotency_key)
        if not duplicate:
            self._flush_outbox()
            self.jobs.submit(job["id"], self._compute_job)
        return job

    def _compute_job(self, job_id: str) -> None:
        job = self._job_by_id(job_id)
        payload = json.loads(job.input_json or "{}")
        if job.kind in {"analysis", "topology"}:
            result = self._analysis_job(job, payload)
            self._write_json_artifact(job, result, f"{job.kind}.json")
            return
        if job.kind == "render":
            self._render_job(job, payload)
            return
        if job.kind == "export":
            result = {"job_id": job_id, "kind": "export", "input": payload}
            self._write_json_artifact(job, result, "export.json")
            return
        raise ValueError(f"unsupported job kind: {job.kind}")

    def _analysis_job(self, job: Any, payload: dict[str, Any]) -> dict[str, Any]:
        structure_id = payload.get("structure_id")
        if not structure_id:
            raise ValueError("analysis job requires structure_id")
        asset = self._asset_by_id(structure_id)
        source_path, temporary = self.object_store.materialize(asset.storage_key)
        try:
            from ..loader.structure_input import load_structure_input

            loaded = load_structure_input(
                str(source_path), input_format=asset.input_format,
                frame_indices=[int(payload.get("frame", 0) or 0)],
            )
            frame = loaded.frames[0]
            bundle = frame.bundle
            result: dict[str, Any] = {
                "job_id": job.id, "kind": job.kind, "structure_id": structure_id,
                "input_format": loaded.input_format, "frame": frame.index,
                "atom_count": len(getattr(bundle, "raw_atoms", None) or []),
                "frame_count": loaded.total_frames,
            }
            if job.kind == "topology":
                center_index = payload.get("center_index")
                if center_index is None:
                    raise ValueError("topology job requires center_index")
                from ..topology import analyze_topology

                result["topology"] = analyze_topology(
                    bundle, int(center_index), float(payload.get("cutoff", 10.0)),
                    ligand_species=payload.get("ligand_species"),
                    level=str(payload.get("level", "molecule")),
                    center_species=payload.get("center_species"),
                )
            return result
        finally:
            if temporary:
                source_path.unlink(missing_ok=True)

    def _render_job(self, job: Any, payload: dict[str, Any]) -> None:
        structure_id = payload.get("structure_id")
        if not structure_id:
            raise ValueError("render job requires structure_id")
        asset = self._asset_by_id(structure_id)
        output = str(payload.get("output") or "png").lower().lstrip(".")
        if output not in {"png", "svg", "pdf", "html"}:
            raise ValueError("render output must be png, svg, pdf, or html")
        with tempfile.TemporaryDirectory(prefix="mattervis-render-") as tmp:
            target = Path(tmp) / f"render.{output}"
            from ..agent import render as render_source
            source_path, temporary = self.object_store.materialize(asset.storage_key)
            try:
                render_source(str(source_path), output=target, backend="plotly" if output == "html" else "cpu")
            finally:
                if temporary:
                    source_path.unlink(missing_ok=True)
            key = f"projects/{job.project_id}/artifacts/{job.id}/render.{output}"
            stored = self.object_store.put_file(key, target)
            self._create_artifact(job, key, f"render.{output}", "text/html" if output == "html" else f"image/{output}", stored.size)

    def _write_json_artifact(self, job: Any, payload: dict[str, Any], filename: str) -> None:
        from ..utils.json_safe import json_safe

        data = json.dumps(json_safe(payload), ensure_ascii=False, indent=2).encode("utf-8")
        key = f"projects/{job.project_id}/artifacts/{job.id}/{filename}"
        stored = self.object_store.put_bytes(key, data)
        self._create_artifact(job, key, filename, "application/json", stored.size)

    def _create_artifact(self, job: Any, key: str, filename: str, content_type: str, size: int) -> dict[str, Any]:
        expires = datetime.now(timezone.utc) + timedelta(days=self.config.artifact_retention_days)
        result = self.repository.create_artifact(project_id=job.project_id, job_id=job.id, kind=job.kind,
                                                 storage_key=key, filename=filename, content_type=content_type,
                                                 size=size, expires_at=expires)
        self._flush_outbox()
        return result

    def get_job(self, auth: AuthContext, project_id: str, job_id: str) -> dict[str, Any]:
        self._project(auth, project_id, "jobs:read")
        return self.repository._job_dict(self.repository.get_job(project_id, job_id))

    def list_job_artifacts(self, auth: AuthContext, project_id: str, job_id: str, base_url: str) -> list[dict[str, Any]]:
        self._project(auth, project_id, "artifacts:read")
        with self.repository.session() as db:
            from .models import Artifact
            artifacts = list(db.scalars(select(Artifact).where(Artifact.project_id == project_id, Artifact.job_id == job_id)))
            return [self.repository._artifact_dict(item, self._artifact_url(item.id, item.storage_key, base_url)) for item in artifacts]

    def _artifact_url(self, artifact_id: str, storage_key: str, base_url: str) -> str:
        if isinstance(self.object_store, S3ObjectStore):
            return self.object_store.signed_url(
                artifact_id=artifact_id, storage_key=storage_key, base_url=base_url,
                ttl_seconds=self.config.artifact_url_ttl_seconds,
            )
        return self.object_store.signed_url(
            artifact_id=artifact_id, base_url=base_url,
            ttl_seconds=self.config.artifact_url_ttl_seconds,
        )

    def artifact_download(self, auth: AuthContext, artifact_id: str, *, expires: int | None = None,
                          signature: str | None = None) -> tuple[Any, dict[str, Any]]:
        with self.repository.session() as db:
            from .models import Artifact
            artifact = db.get(Artifact, artifact_id)
            if artifact is None:
                raise NotFoundError("artifact not found")
            if artifact.expires_at is not None:
                expires_at = artifact.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < datetime.now(timezone.utc):
                    raise NotFoundError("artifact has expired")
            if auth.auth_type == "signed-url":
                pass
            elif auth.is_api_key:
                self._project(auth, artifact.project_id, "artifacts:read")
            elif auth.auth_type == "dev" or auth.auth_type == "oidc" or auth.auth_type == "proxy":
                self._project(auth, artifact.project_id, "artifacts:read")
            if expires is not None and signature is not None and not self.object_store.verify_signature(
                artifact_id=artifact_id, expires=expires, signature=signature
            ):
                raise AuthError("invalid artifact signature")
            return self.object_store.open(artifact.storage_key), self.repository._artifact_dict(artifact)

    def create_api_key(self, auth: AuthContext, project_id: str, name: str, scopes: list[str]) -> tuple[str, dict[str, Any]]:
        if auth.is_api_key:
            raise AuthorizationError("API keys cannot create API keys")
        self._project(auth, project_id)
        return self.api_keys.create(project_id, name, scopes)

    def revoke_api_key(self, auth: AuthContext, project_id: str, key_id: str) -> None:
        if auth.is_api_key:
            raise AuthorizationError("API keys cannot revoke API keys")
        self._project(auth, project_id)
        self.api_keys.revoke(project_id, key_id)
