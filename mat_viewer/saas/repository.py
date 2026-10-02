"""Persistence boundary for tenant-scoped MatterVis resources."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import secrets
import threading
from typing import Any, Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    Artifact,
    Base,
    IdempotencyRecord,
    Job,
    Project,
    ProjectApiKey,
    Scene,
    StructureAsset,
    User,
    Workspace,
    OutboxEvent,
    new_id,
    utcnow,
)


class RepositoryError(RuntimeError):
    status_code = 400


class NotFoundError(RepositoryError):
    status_code = 404


class ConflictError(RepositoryError):
    status_code = 409


class AuthorizationError(RepositoryError):
    status_code = 403


def json_loads(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed
    except Exception:
        return fallback


class SaaSRepository:
    """SQLAlchemy repository with SQLite, PostgreSQL, and test compatibility.

    The service uses only this class.  Swapping the SQLAlchemy URL from the
    default local SQLite database to PostgreSQL does not change API behavior.
    """

    def __init__(self, database_url: str):
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, future=True, connect_args=connect_args)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False, class_=Session)
        self._lock = threading.RLock()
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._lock:
            session = self.session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _outbox(db: Session, project_id: str, event: dict[str, Any]) -> None:
        db.add(OutboxEvent(id=new_id("event"), project_id=project_id,
                           event_json=json.dumps(event, ensure_ascii=False)))

    def drain_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.session() as db:
            rows = list(db.scalars(select(OutboxEvent).where(
                OutboxEvent.published_at.is_(None)
            ).order_by(OutboxEvent.created_at).limit(int(limit))))
            events = []
            for row in rows:
                event = json_loads(row.event_json, {})
                event["project_id"] = row.project_id
                events.append(event)
                row.published_at = utcnow()
            return events

    @staticmethod
    def _workspace_dict(workspace: Workspace) -> dict[str, Any]:
        return {
            "id": workspace.id,
            "name": workspace.name,
            "owner_subject": workspace.owner_subject,
            "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        }

    @staticmethod
    def _project_dict(project: Project) -> dict[str, Any]:
        return {
            "id": project.id,
            "workspace_id": project.workspace_id,
            "name": project.name,
            "visibility": project.visibility,
            "created_at": project.created_at.isoformat() if project.created_at else None,
        }

    @staticmethod
    def _asset_dict(asset: StructureAsset) -> dict[str, Any]:
        return {
            "id": asset.id,
            "project_id": asset.project_id,
            "original_filename": asset.original_filename,
            "input_format": asset.input_format,
            "sha256": asset.sha256,
            "status": asset.status,
            "atom_count": asset.atom_count,
            "frame_count": asset.frame_count,
            "size_bytes": asset.size_bytes,
            "metadata": asset.metadata_payload,
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
        }

    @staticmethod
    def _scene_dict(scene: Scene) -> dict[str, Any]:
        return {
            "id": scene.id,
            "project_id": scene.project_id,
            "structure_id": scene.structure_asset_id,
            "label": scene.label,
            "state": json_loads(scene.state_json, {}),
            "revision": scene.revision,
            "created_at": scene.created_at.isoformat() if scene.created_at else None,
            "updated_at": scene.updated_at.isoformat() if scene.updated_at else None,
        }

    @staticmethod
    def _job_dict(job: Job) -> dict[str, Any]:
        return {
            "id": job.id,
            "project_id": job.project_id,
            "kind": job.kind,
            "status": job.status,
            "progress": float(job.progress),
            "input": json_loads(job.input_json, {}),
            "error": json_loads(job.error_json, {}),
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }

    @staticmethod
    def _artifact_dict(artifact: Artifact, download_url: str | None = None) -> dict[str, Any]:
        result = {
            "id": artifact.id,
            "project_id": artifact.project_id,
            "job_id": artifact.job_id,
            "kind": artifact.kind,
            "filename": artifact.filename,
            "content_type": artifact.content_type,
            "size": artifact.size,
            "expires_at": artifact.expires_at.isoformat() if artifact.expires_at else None,
            "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        }
        if download_url is not None:
            result["download_url"] = download_url
        return result

    def ensure_principal(self, subject: str, display_name: str | None = None) -> dict[str, Any]:
        subject = str(subject).strip()
        if not subject:
            raise RepositoryError("principal subject is required")
        with self.session() as db:
            user = db.get(User, subject)
            if user is None:
                user = User(subject=subject, display_name=display_name or subject)
                db.add(user)
            workspace = db.scalar(
                select(Workspace).where(Workspace.owner_subject == subject).order_by(Workspace.created_at)
            )
            if workspace is None:
                workspace = Workspace(id=new_id("ws"), owner_subject=subject, name="Personal workspace")
                db.add(workspace)
                db.flush()
            project = db.scalar(
                select(Project)
                .where(Project.workspace_id == workspace.id)
                .order_by(Project.created_at)
            )
            if project is None:
                project = Project(id=new_id("proj"), workspace_id=workspace.id, name="My structures")
                db.add(project)
                db.flush()
            return {
                "user": {"subject": user.subject, "display_name": user.display_name},
                "workspace": self._workspace_dict(workspace),
                "project": self._project_dict(project),
            }

    def list_workspaces(self, subject: str) -> list[dict[str, Any]]:
        with self.session() as db:
            return [self._workspace_dict(item) for item in db.scalars(
                select(Workspace).where(Workspace.owner_subject == subject).order_by(Workspace.created_at)
            )]

    def create_project(self, subject: str, workspace_id: str, name: str) -> dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise RepositoryError("project name is required")
        with self.session() as db:
            workspace = db.scalar(
                select(Workspace).where(
                    Workspace.id == workspace_id, Workspace.owner_subject == subject
                )
            )
            if workspace is None:
                raise AuthorizationError("workspace is not owned by this user")
            project = Project(id=new_id("proj"), workspace_id=workspace_id, name=name)
            db.add(project)
            db.flush()
            return self._project_dict(project)

    def list_projects(self, subject: str, workspace_id: str) -> list[dict[str, Any]]:
        with self.session() as db:
            workspace = db.scalar(
                select(Workspace).where(
                    Workspace.id == workspace_id, Workspace.owner_subject == subject
                )
            )
            if workspace is None:
                raise AuthorizationError("workspace is not owned by this user")
            return [self._project_dict(item) for item in db.scalars(
                select(Project).where(Project.workspace_id == workspace_id).order_by(Project.created_at)
            )]

    def project_for_subject(self, subject: str, project_id: str) -> Project:
        with self.session() as db:
            project = db.get(Project, project_id)
            if project is None:
                raise NotFoundError("project not found")
            workspace = db.get(Workspace, project.workspace_id)
            if workspace is None or workspace.owner_subject != subject:
                raise AuthorizationError("project is not accessible")
            return self._project_dict(project)  # type: ignore[return-value]

    def project_for_api_key(self, project_id: str) -> dict[str, Any]:
        with self.session() as db:
            project = db.get(Project, project_id)
            if project is None:
                raise NotFoundError("project not found")
            return self._project_dict(project)

    def find_asset_by_hash(self, project_id: str, digest: str) -> dict[str, Any] | None:
        with self.session() as db:
            asset = db.scalar(select(StructureAsset).where(
                StructureAsset.project_id == project_id, StructureAsset.sha256 == digest
            ))
            return self._asset_dict(asset) if asset else None

    def create_asset(self, *, project_id: str, filename: str, input_format: str | None,
                     digest: str, storage_key: str, size_bytes: int = 0) -> dict[str, Any]:
        with self.session() as db:
            asset = StructureAsset(
                id=new_id("asset"), project_id=project_id, original_filename=filename,
                input_format=input_format, sha256=digest, storage_key=storage_key,
                size_bytes=int(size_bytes),
            )
            db.add(asset)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                existing = db.scalar(select(StructureAsset).where(
                    StructureAsset.project_id == project_id, StructureAsset.sha256 == digest
                ))
                if existing is None:
                    raise
                return self._asset_dict(existing)
            return self._asset_dict(asset)

    def get_asset(self, project_id: str, asset_id: str) -> StructureAsset:
        with self.session() as db:
            asset = db.scalar(select(StructureAsset).where(
                StructureAsset.id == asset_id, StructureAsset.project_id == project_id
            ))
            if asset is None:
                raise NotFoundError("structure asset not found")
            return asset  # type: ignore[return-value]

    def list_assets(self, project_id: str) -> list[dict[str, Any]]:
        with self.session() as db:
            return [self._asset_dict(item) for item in db.scalars(
                select(StructureAsset).where(StructureAsset.project_id == project_id).order_by(StructureAsset.created_at)
            )]

    def update_asset(self, asset_id: str, **values: Any) -> dict[str, Any]:
        with self.session() as db:
            asset = db.get(StructureAsset, asset_id)
            if asset is None:
                raise NotFoundError("structure asset not found")
            for key, value in values.items():
                if key == "metadata":
                    value = json.dumps(value or {}, ensure_ascii=False)
                    key = "metadata_json"
                setattr(asset, key, value)
            db.flush()
            self._outbox(db, asset.project_id, {"type": "structure.updated", "structure": self._asset_dict(asset)})
            return self._asset_dict(asset)

    def create_scene(self, *, project_id: str, structure_id: str, label: str,
                     state: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.session() as db:
            asset = db.scalar(select(StructureAsset).where(
                StructureAsset.id == structure_id, StructureAsset.project_id == project_id
            ))
            if asset is None:
                raise NotFoundError("structure asset not found")
            scene = Scene(
                id=new_id("scene"), project_id=project_id, structure_asset_id=structure_id,
                label=str(label or asset.original_filename), state_json=json.dumps(state or {}, ensure_ascii=False),
            )
            db.add(scene)
            db.flush()
            self._outbox(db, project_id, {"type": "scene.updated", "scene": self._scene_dict(scene)})
            return self._scene_dict(scene)

    def list_scenes(self, project_id: str) -> list[dict[str, Any]]:
        with self.session() as db:
            return [self._scene_dict(item) for item in db.scalars(
                select(Scene).where(Scene.project_id == project_id).order_by(Scene.created_at)
            )]

    def get_scene(self, project_id: str, scene_id: str) -> Scene:
        with self.session() as db:
            scene = db.scalar(select(Scene).where(Scene.id == scene_id, Scene.project_id == project_id))
            if scene is None:
                raise NotFoundError("scene not found")
            return scene  # type: ignore[return-value]

    def patch_scene(self, *, project_id: str, scene_id: str, patch: dict[str, Any], expected_revision: int | None) -> dict[str, Any]:
        with self.session() as db:
            scene = db.scalar(select(Scene).where(Scene.id == scene_id, Scene.project_id == project_id))
            if scene is None:
                raise NotFoundError("scene not found")
            if expected_revision is not None and scene.revision != expected_revision:
                raise ConflictError(f"scene revision mismatch: expected {expected_revision}, current {scene.revision}")
            current = json_loads(scene.state_json, {})
            current.update(patch.get("state") or patch)
            if "label" in patch:
                scene.label = str(patch["label"] or scene.label)
            scene.state_json = json.dumps(current, ensure_ascii=False)
            scene.revision += 1
            scene.updated_at = utcnow()
            db.flush()
            self._outbox(db, project_id, {"type": "scene.updated", "scene": self._scene_dict(scene)})
            return self._scene_dict(scene)

    def create_job(self, *, project_id: str, kind: str, payload: dict[str, Any], idempotency_key: str | None = None) -> tuple[dict[str, Any], bool]:
        with self.session() as db:
            if idempotency_key:
                existing = db.scalar(select(IdempotencyRecord).where(
                    IdempotencyRecord.project_id == project_id,
                    IdempotencyRecord.key == idempotency_key,
                    IdempotencyRecord.operation == kind,
                ))
                if existing is not None:
                    job = db.get(Job, existing.resource_id)
                    if job is not None:
                        return self._job_dict(job), True
            job = Job(id=new_id("job"), project_id=project_id, kind=kind,
                      input_json=json.dumps(payload or {}, ensure_ascii=False),
                      idempotency_key=idempotency_key)
            db.add(job)
            db.flush()
            if idempotency_key:
                db.add(IdempotencyRecord(id=new_id("idem"), project_id=project_id,
                                         key=idempotency_key, operation=kind, resource_id=job.id))
            self._outbox(db, project_id, {"type": "job.updated", "job": self._job_dict(job)})
            return self._job_dict(job), False

    def get_job(self, project_id: str, job_id: str) -> Job:
        with self.session() as db:
            job = db.scalar(select(Job).where(Job.id == job_id, Job.project_id == project_id))
            if job is None:
                raise NotFoundError("job not found")
            return job  # type: ignore[return-value]

    def update_job(self, job_id: str, **values: Any) -> dict[str, Any]:
        with self.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFoundError("job not found")
            for key, value in values.items():
                if key in {"input", "error"}:
                    value = json.dumps(value or {}, ensure_ascii=False)
                    key = f"{key}_json"
                setattr(job, key, value)
            job.updated_at = utcnow()
            db.flush()
            self._outbox(db, job.project_id, {"type": "job.updated", "job": self._job_dict(job)})
            return self._job_dict(job)

    def create_artifact(self, *, project_id: str, job_id: str | None, kind: str,
                        storage_key: str, filename: str, content_type: str, size: int,
                        expires_at: datetime | None) -> dict[str, Any]:
        with self.session() as db:
            artifact = Artifact(id=new_id("artifact"), project_id=project_id, job_id=job_id,
                                kind=kind, storage_key=storage_key, filename=filename,
                                content_type=content_type, size=size, expires_at=expires_at)
            db.add(artifact)
            db.flush()
            self._outbox(db, project_id, {"type": "artifact.ready", "artifact": self._artifact_dict(artifact)})
            return self._artifact_dict(artifact)

    def get_artifact(self, project_id: str, artifact_id: str) -> Artifact:
        with self.session() as db:
            artifact = db.scalar(select(Artifact).where(
                Artifact.id == artifact_id, Artifact.project_id == project_id
            ))
            if artifact is None:
                raise NotFoundError("artifact not found")
            return artifact  # type: ignore[return-value]

    def add_api_key(self, *, project_id: str, name: str, scopes: list[str]) -> tuple[str, dict[str, Any]]:
        raw = f"mvk_{secrets.token_urlsafe(24)}"
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        with self.session() as db:
            row = ProjectApiKey(id=new_id("key"), project_id=project_id, prefix=raw[:12],
                                key_hash=digest, name=name or "MatterVis API key",
                                scopes_json=json.dumps(sorted(set(scopes))))
            db.add(row)
            db.flush()
            return raw, {"id": row.id, "prefix": row.prefix, "name": row.name,
                         "scopes": json_loads(row.scopes_json, []), "created_at": row.created_at.isoformat()}

    def authenticate_api_key(self, raw: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        with self.session() as db:
            row = db.scalar(select(ProjectApiKey).where(
                ProjectApiKey.key_hash == digest, ProjectApiKey.revoked_at.is_(None)
            ))
            if row is None:
                return None
            return {"subject": f"api-key:{row.id}", "project_id": row.project_id,
                    "scopes": json_loads(row.scopes_json, []) or [], "key_id": row.id}

    def revoke_api_key(self, project_id: str, key_id: str) -> None:
        with self.session() as db:
            row = db.scalar(select(ProjectApiKey).where(
                ProjectApiKey.id == key_id, ProjectApiKey.project_id == project_id
            ))
            if row is None:
                raise NotFoundError("API key not found")
            row.revoked_at = utcnow()
