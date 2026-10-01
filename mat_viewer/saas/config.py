"""Configuration for the hosted MatterVis service.

Values are intentionally environment-driven.  The defaults are conservative
development limits and are not a billing policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True, slots=True)
class SaaSConfig:
    """Runtime configuration shared by API, storage and job services."""

    database_url: str
    object_store_root: str
    object_store_bucket: str
    object_store_backend: str
    s3_endpoint_url: str | None
    s3_region: str | None
    s3_access_key: str | None
    s3_secret_key: str | None
    redis_url: str | None
    auth_mode: str
    oidc_issuer_url: str | None
    oidc_audience: str | None
    artifact_signing_key: str
    artifact_url_ttl_seconds: int = 900
    artifact_retention_days: int = 7
    max_upload_bytes: int = 100 * 1024 * 1024
    max_atoms_per_frame: int = 200_000
    max_trajectory_frames: int = 2_000
    max_storage_bytes: int = 10 * 1024 * 1024 * 1024
    max_concurrent_jobs: int = 4
    monthly_cpu_seconds: int = 7_200
    job_timeout_seconds: int = 900

    @classmethod
    def from_env(cls, root_dir: str | None = None) -> "SaaSConfig":
        root = Path(root_dir or os.environ.get("MATTERVIS_ROOT", ".")).resolve()
        local = root / ".local"
        local.mkdir(parents=True, exist_ok=True)
        return cls(
            database_url=os.environ.get(
                "MATTERVIS_DATABASE_URL",
                f"sqlite:///{(local / 'mattervis_saas.sqlite3').as_posix()}",
            ),
            object_store_root=os.environ.get(
                "MATTERVIS_OBJECT_STORE_ROOT", str(local / "objects")
            ),
            object_store_bucket=os.environ.get("MATTERVIS_OBJECT_STORE_BUCKET", "mattervis"),
            object_store_backend=os.environ.get("MATTERVIS_OBJECT_STORE_BACKEND", "local").strip().lower(),
            s3_endpoint_url=os.environ.get("MATTERVIS_S3_ENDPOINT_URL"),
            s3_region=os.environ.get("MATTERVIS_S3_REGION"),
            s3_access_key=os.environ.get("MATTERVIS_S3_ACCESS_KEY"),
            s3_secret_key=os.environ.get("MATTERVIS_S3_SECRET_KEY"),
            redis_url=os.environ.get("MATTERVIS_REDIS_URL"),
            auth_mode=os.environ.get("MATTERVIS_AUTH_MODE", "dev").strip().lower(),
            oidc_issuer_url=os.environ.get("MATTERVIS_OIDC_ISSUER_URL"),
            oidc_audience=os.environ.get("MATTERVIS_OIDC_AUDIENCE"),
            artifact_signing_key=os.environ.get(
                "MATTERVIS_ARTIFACT_SIGNING_KEY", "mattervis-development-signing-key"
            ),
            artifact_url_ttl_seconds=_env_int("MATTERVIS_ARTIFACT_URL_TTL", 900),
            artifact_retention_days=_env_int("MATTERVIS_ARTIFACT_RETENTION_DAYS", 7),
            max_upload_bytes=_env_int("MATTERVIS_MAX_UPLOAD_BYTES", 100 * 1024 * 1024),
            max_atoms_per_frame=_env_int("MATTERVIS_MAX_ATOMS_PER_FRAME", 200_000),
            max_trajectory_frames=_env_int("MATTERVIS_MAX_TRAJECTORY_FRAMES", 2_000),
            max_storage_bytes=_env_int(
                "MATTERVIS_MAX_STORAGE_BYTES", 10 * 1024 * 1024 * 1024
            ),
            max_concurrent_jobs=_env_int("MATTERVIS_MAX_CONCURRENT_JOBS", 4),
            monthly_cpu_seconds=_env_int("MATTERVIS_MONTHLY_CPU_SECONDS", 7_200),
            job_timeout_seconds=_env_int("MATTERVIS_JOB_TIMEOUT_SECONDS", 900),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_url": self.database_url,
            "object_store_bucket": self.object_store_bucket,
            "object_store_backend": self.object_store_backend,
            "redis_configured": bool(self.redis_url),
            "auth_mode": self.auth_mode,
            "artifact_url_ttl_seconds": self.artifact_url_ttl_seconds,
            "artifact_retention_days": self.artifact_retention_days,
            "max_upload_bytes": self.max_upload_bytes,
            "max_atoms_per_frame": self.max_atoms_per_frame,
            "max_trajectory_frames": self.max_trajectory_frames,
            "max_storage_bytes": self.max_storage_bytes,
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "monthly_cpu_seconds": self.monthly_cpu_seconds,
            "job_timeout_seconds": self.job_timeout_seconds,
        }
