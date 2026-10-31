"""Warn-only check that code changes ship docs or an explicit exemption.

The process exits 0 even when it warns. A missing docs update is a review
finding, not a build failure.

Code paths are ``mat_viewer/`` (the library and the Dash/REST server under
``mat_viewer/app/`` and ``mat_viewer/api/``), ``frontend/``, and a top-level
``server/`` tree. Doc paths are ``docs/**``, ``AGENTS.md``, and
``CONTRIBUTING.md``.

The exemption is a visible ``Docs: N/A`` in the pull request body. HTML
comments are ignored, and the unfilled pull-request-template label is
removed before the search, so the template's instructions do not satisfy
the check.
"""

from __future__ import annotations

import os
import re
import subprocess

CODE_PREFIXES = ("mat_viewer/", "frontend/", "server/")
DOC_FILES = {"AGENTS.md", "CONTRIBUTING.md"}
EXEMPTION = "Docs: N/A"
UNFILLED_DOCS_LABEL = "- Docs paths touched, or `Docs: N/A` + one-line reason:"

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

WARNING = (
    "Docs policy: mat_viewer/, frontend/, or server code changed, but no "
    "docs/**, AGENTS.md, or CONTRIBUTING.md file changed, and the pull "
    "request body has no substantive Docs: N/A. In-scope render, CLI, "
    "WebGL session/upload, architecture, or display-policy changes need "
    "stable docs in this pull request. Internal-only changes should include "
    "Docs: N/A and a one-line reason. This check does not fail the build."
)


def normalize(path: str) -> str:
    return path.replace("\\", "/").strip().removeprefix("./")


def is_code_path(path: str) -> bool:
    norm = normalize(path)
    return any(norm.startswith(prefix) for prefix in CODE_PREFIXES)


def is_docs_path(path: str) -> bool:
    norm = normalize(path)
    return norm in DOC_FILES or norm.startswith("docs/")


def substantive_body(body: str) -> str:
    """Return pull-request text that can actually carry an exemption."""
    visible = _HTML_COMMENT.sub("", body or "")
    visible = visible.replace("\r\n", "\n").replace("\r", "\n")
    return visible.replace(UNFILLED_DOCS_LABEL, "")


def has_exemption(body: str) -> bool:
    return EXEMPTION in substantive_body(body)


def evaluate(paths: list[str], body: str) -> str | None:
    """Return a warning, or None when the check is satisfied or not applicable."""
    if not any(is_code_path(path) for path in paths):
        return None
    if any(is_docs_path(path) for path in paths):
        return None
    if has_exemption(body):
        return None
    return WARNING


def paths_from_name_status_z(blob: bytes) -> list[str]:
    """Parse ``git diff --name-status -z`` output, including rename pairs."""
    parts = blob.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(parts):
        status = parts[index].decode("utf-8", "replace")
        if not status:
            index += 1
            continue
        index += 1
        needed = 2 if status[0] in {"R", "C"} else 1
        for _ in range(needed):
            if index >= len(parts) or not parts[index]:
                break
            paths.append(parts[index].decode("utf-8", "replace"))
            index += 1
    return paths


def load_changed_paths() -> list[str]:
    if "DOCS_POLICY_PATHS" in os.environ:
        return [
            line.strip()
            for line in os.environ["DOCS_POLICY_PATHS"].splitlines()
            if line.strip()
        ]

    base_ref = os.environ.get("DOCS_POLICY_BASE_REF", "").strip()
    base = os.environ.get("DOCS_POLICY_BASE", "").strip()
    head = os.environ.get("DOCS_POLICY_HEAD", "HEAD").strip() or "HEAD"
    if base_ref:
        fetch = subprocess.run(
            ["git", "fetch", "origin", base_ref],
            check=False,
            capture_output=True,
            text=True,
        )
        if fetch.returncode != 0:
            detail = (fetch.stderr or fetch.stdout or "").strip()
            raise RuntimeError(f"git fetch origin {base_ref} failed: {detail}")
        if not base:
            base = f"origin/{base_ref}"
    if not base:
        raise RuntimeError("DOCS_POLICY_BASE or DOCS_POLICY_PATHS is required")

    diff = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "--find-renames",
            "-z",
            f"{base}...{head}",
        ],
        check=False,
        capture_output=True,
    )
    if diff.returncode != 0:
        detail = (diff.stderr or diff.stdout or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"git diff failed: {detail}")
    return paths_from_name_status_z(diff.stdout)


def emit_warning(message: str) -> None:
    print(message)
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning::{escaped}")


def main() -> int:
    try:
        paths = load_changed_paths()
        body = os.environ.get("PR_BODY", "")
        warning = evaluate(paths, body)
    except Exception as exc:  # noqa: BLE001 — a warn-only check must not fail the build
        emit_warning(
            "Docs policy check could not run "
            f"({exc}). This check does not fail the build."
        )
        return 0

    if warning:
        emit_warning(warning)
        return 0
    if not any(is_code_path(path) for path in paths):
        print("Docs policy check skipped: no mat_viewer/, frontend/, or server changes.")
        return 0
    if any(is_docs_path(path) for path in paths):
        print("Docs policy check passed: documentation files changed.")
        return 0
    print("Docs policy check passed: pull request body contains Docs: N/A.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
