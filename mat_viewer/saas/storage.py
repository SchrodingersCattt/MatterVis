"""Object storage adapters and signed artifact URLs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import shutil
import tempfile
from typing import BinaryIO
from urllib.parse import urlencode


class ObjectStoreError(RuntimeError):
    status_code = 500


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size: int
    sha256: str


class LocalObjectStore:
    """Filesystem adapter used by tests, local development, and MinIO-like paths."""

    def __init__(self, root: str, signing_key: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.signing_key = signing_key.encode("utf-8")

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ObjectStoreError("object key escapes storage root", status_code=400)
        return candidate

    def put_bytes(self, key: str, data: bytes) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(key=key, size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def put_file(self, key: str, source: str | Path, *, max_bytes: int | None = None) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with open(source, "rb") as reader, open(path, "wb") as writer:
            while True:
                block = reader.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if max_bytes is not None and size > max_bytes:
                    writer.close()
                    path.unlink(missing_ok=True)
                    raise ObjectStoreError("object exceeds configured size limit", status_code=413)
                digest.update(block)
                writer.write(block)
        return StoredObject(key=key, size=size, sha256=digest.hexdigest())

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def materialize(self, key: str) -> tuple[Path, bool]:
        """Return a local source path and whether the caller must remove it."""
        return self._path(key), False

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def signed_url(self, *, artifact_id: str, base_url: str, ttl_seconds: int) -> str:
        expires = int((datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).timestamp())
        message = f"{artifact_id}:{expires}".encode("utf-8")
        signature = hmac.new(self.signing_key, message, hashlib.sha256).hexdigest()
        return f"{base_url.rstrip('/')}/api/artifacts/{artifact_id}/download?{urlencode({'expires': expires, 'signature': signature})}"

    def verify_signature(self, *, artifact_id: str, expires: int, signature: str) -> bool:
        if expires < int(datetime.now(timezone.utc).timestamp()):
            return False
        expected = hmac.new(self.signing_key, f"{artifact_id}:{expires}".encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, str(signature))


class S3ObjectStore:
    """S3-compatible adapter with presigned artifact delivery.

    ``boto3`` is imported lazily so local and test installs do not need a
    network client.  MinIO works by setting ``endpoint_url`` and credentials.
    """

    def __init__(self, *, bucket: str, endpoint_url: str | None = None,
                 region_name: str | None = None, access_key: str | None = None,
                 secret_key: str | None = None):
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region_name,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        )

    def put_bytes(self, key: str, data: bytes) -> StoredObject:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return StoredObject(key=key, size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def put_file(self, key: str, source: str | Path, *, max_bytes: int | None = None) -> StoredObject:
        size = Path(source).stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise ObjectStoreError("object exceeds configured size limit", status_code=413)
        digest = hashlib.sha256()
        with open(source, "rb") as handle:
            data = handle.read()
        digest.update(data)
        self.client.upload_file(str(source), self.bucket, key)
        return StoredObject(key=key, size=size, sha256=digest.hexdigest())

    def open(self, key: str) -> BinaryIO:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        handle = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        shutil.copyfileobj(response["Body"], handle)
        handle.seek(0)
        return handle

    def materialize(self, key: str) -> tuple[Path, bool]:
        handle = self.open(key)
        suffix = Path(key).suffix
        fd, name = tempfile.mkstemp(prefix="mattervis-object-", suffix=suffix)
        import os

        os.close(fd)
        target = Path(name)
        try:
            with open(target, "wb") as writer:
                shutil.copyfileobj(handle, writer)
        finally:
            handle.close()
        return target, True

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, *, artifact_id: str, base_url: str, ttl_seconds: int, storage_key: str | None = None) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": storage_key or artifact_id},
            ExpiresIn=ttl_seconds,
        )
