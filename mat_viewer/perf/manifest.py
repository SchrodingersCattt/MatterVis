"""Versioned benchmark fixture manifest and integrity checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "mattervis.benchmarks/v1"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "benchmarks" / "mattervis_manifest.json"


def _fixture_sha256(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    # Git stores the text fixtures with LF.  Windows checkouts may materialize
    # CRLF, so normalize only known text formats before hashing; binary inputs
    # retain their exact byte digest.
    raw_digest = hashlib.sha256(data).hexdigest()
    if path.suffix.lower() in {".cif", ".xyz", ".extxyz", ".vasp", ".json"}:
        data = data.replace(b"\r\n", b"\n")
    return raw_digest, hashlib.sha256(data).hexdigest()


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path or DEFAULT_MANIFEST).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported benchmark manifest schema: {payload.get('schema')!r}")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("benchmark manifest must contain a non-empty entries list")
    seen: set[str] = set()
    seen_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("benchmark manifest entries must be objects")
        entry_id = str(entry.get("id") or "")
        relative = str(entry.get("path") or "")
        if not entry_id or entry_id in seen or not relative or relative in seen_paths:
            raise ValueError("benchmark manifest entries require unique id/path values")
        if not entry.get("category") or not entry.get("size_class"):
            raise ValueError(f"benchmark fixture {entry_id!r} is missing category/size_class")
        seen.add(entry_id)
        seen_paths.add(relative)
    payload["path"] = str(manifest_path)
    return payload


def fixture_paths(
    manifest: dict[str, Any] | None = None,
    *,
    root: str | Path | None = None,
    verify: bool = True,
) -> list[dict[str, Any]]:
    manifest = load_manifest() if manifest is None else manifest
    root_path = Path(root or Path(__file__).resolve().parents[2]).resolve()
    resolved: list[dict[str, Any]] = []
    for raw in manifest["entries"]:
        entry = dict(raw)
        path = (root_path / str(entry["path"])).resolve()
        if root_path not in path.parents and path != root_path:
            raise ValueError(f"benchmark fixture escapes repository root: {path}")
        if not path.is_file():
            raise FileNotFoundError(path)
        raw_digest, normalized_digest = _fixture_sha256(path)
        expected = str(entry.get("sha256") or "").lower()
        if verify and expected and expected not in {raw_digest, normalized_digest}:
            raise ValueError(
                f"benchmark fixture {entry['id']!r} checksum mismatch: "
                f"expected {expected}, got {raw_digest}"
            )
        entry["path"] = str(path)
        entry["sha256"] = expected or normalized_digest
        resolved.append(entry)
    return resolved


__all__ = ["DEFAULT_MANIFEST", "SCHEMA", "fixture_paths", "load_manifest"]
