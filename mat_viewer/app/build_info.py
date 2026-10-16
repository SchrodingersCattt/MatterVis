"""Process identity versus the frontend assets currently on disk.

``build_sha`` freezes the first fingerprint this process computed.
``asset_sha`` recomputes the asset half. They diverge when a running
server keeps its imported code while Flask serves newer files from
disk. The index page compares them and tells the user to restart the
server, then reload.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

_FROZEN: str | None = None
_GIT_HEAD: str | None = None
_MISMATCH_TEXT = (
    "This server is still running older code than the files on disk. "
    "Restart the server, then reload this page."
)


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _asset_dir() -> str:
    return os.path.join(_repo_root(), "frontend", "assets")


def _git_head() -> str:
    global _GIT_HEAD
    if _GIT_HEAD is not None:
        return _GIT_HEAD
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _GIT_HEAD = ""
        return _GIT_HEAD
    _GIT_HEAD = proc.stdout.strip() if proc.returncode == 0 else ""
    return _GIT_HEAD


def _asset_stat_token(asset_dir: str) -> str:
    hasher = hashlib.sha256()
    if os.path.isdir(asset_dir):
        for dirpath, dirnames, filenames in os.walk(asset_dir):
            dirnames.sort()
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, asset_dir).replace(os.sep, "/")
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                hasher.update(rel.encode())
                hasher.update(str(stat.st_size).encode())
                hasher.update(str(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))).encode())
    return hasher.hexdigest()


def compute_fingerprint(asset_dir: str | None = None) -> str:
    """Hash git HEAD plus each asset path, size, and mtime."""
    directory = asset_dir or _asset_dir()
    hasher = hashlib.sha256()
    hasher.update(_git_head().encode())
    hasher.update(_asset_stat_token(directory).encode())
    return hasher.hexdigest()


def build_sha() -> str:
    """Fingerprint frozen at the first call in this process."""
    global _FROZEN
    if _FROZEN is None:
        _FROZEN = compute_fingerprint()
    return _FROZEN


def asset_sha() -> str:
    """Fingerprint of the assets on disk right now."""
    return compute_fingerprint()


def asset_url_token() -> str:
    """URL-safe token derived from the frozen process fingerprint."""
    return build_sha()[:16]


def install_index_identity(app) -> None:
    """Embed the two fingerprints in the index and keep that HTML uncached."""
    original = app.interpolate_index

    def interpolate_index(**kwargs):
        html = original(**kwargs)
        payload = json.dumps(
            {"process": build_sha(), "assets": asset_sha()},
            separators=(",", ":"),
        )
        script = (
            "<script>window.MATTERVIS_BUILD="
            + payload
            + ";(function(){var b=window.MATTERVIS_BUILD;if(!b||b.process===b.assets)return;"
            "function show(){if(document.getElementById('mv-build-mismatch'))return;"
            "var n=document.createElement('div');n.id='mv-build-mismatch';"
            "n.setAttribute('role','status');n.textContent="
            + json.dumps(_MISMATCH_TEXT)
            + ";n.style.cssText='position:sticky;top:0;z-index:10000;background:#7a1f1f;"
            "color:#fff;padding:10px 14px;font:14px/1.4 sans-serif';"
            "(document.body||document.documentElement).prepend(n);}"
            "if(document.body)show();else document.addEventListener('DOMContentLoaded',show);"
            "})();</script>"
        )
        if "</head>" in html:
            return html.replace("</head>", script + "</head>", 1)
        return script + html

    app.interpolate_index = interpolate_index

    @app.server.after_request
    def _index_no_store(response):
        from flask import request

        path = request.path or ""
        prefix = str(getattr(app.config, "routes_pathname_prefix", "/") or "/")
        if path == prefix.rstrip("/") or path == prefix or path in {"", "/"}:
            response.headers["Cache-Control"] = "no-store"
        return response
