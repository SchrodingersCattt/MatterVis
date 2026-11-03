"""Write the resolved interpreter, MolCrysKit revision, and toolchain.

The script is diagnostic. A missing optional tool is recorded as null.
It does not skip tests and it does not change their results.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path


def _molcrys() -> dict[str, object]:
    try:
        dist = distribution("molcrys-kit")
    except PackageNotFoundError:
        return {"installed": False}
    raw = dist.read_text("direct_url.json")
    direct = json.loads(raw) if raw else None
    return {
        "installed": True,
        "version": version("molcrys-kit"),
        "direct_url": direct,
    }


def _tool_version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    text = (completed.stdout or completed.stderr).strip()
    if not text:
        return None
    return text.splitlines()[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ci-diagnostics")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    attempt_raw = os.environ.get("GITHUB_RUN_ATTEMPT")
    attempt = int(attempt_raw) if attempt_raw and attempt_raw.isdigit() else None
    note = (
        "run_attempt greater than 1 is a rerun recovery, not a clean "
        "first-attempt success. A cancelled workflow is concurrency "
        "cancel-in-progress, not a test flake."
    )
    payload = {
        "python": sys.version,
        "platform": platform.platform(),
        "molcrys_kit": _molcrys(),
        "node": _tool_version(["node", "--version"]),
        "chrome": _tool_version(["google-chrome", "--version"])
        or _tool_version(["google-chrome-stable", "--version"]),
        "declared": {
            "node": os.environ.get("CI_NODE_VERSION"),
            "chrome": os.environ.get("CI_CHROME_VERSION"),
            "playwright_core": os.environ.get("CI_PLAYWRIGHT_CORE"),
            "pip": os.environ.get("CI_PIP_VERSION"),
            "ubuntu": "ubuntu-24.04",
        },
        "github": {
            "workflow": os.environ.get("GITHUB_WORKFLOW"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "run_attempt": attempt,
            "event_name": os.environ.get("GITHUB_EVENT_NAME"),
            "ref": os.environ.get("GITHUB_REF"),
            "sha": os.environ.get("GITHUB_SHA"),
            "first_attempt": None if attempt is None else attempt == 1,
        },
        "note": note,
    }
    (output_dir / "ci-environment.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "ci-attempt.json").write_text(
        json.dumps(
            {
                "run_id": payload["github"]["run_id"],
                "run_attempt": attempt,
                "first_attempt": payload["github"]["first_attempt"],
                "event_name": payload["github"]["event_name"],
                "note": note,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    freeze = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        check=False,
        capture_output=True,
        text=True,
    )
    freeze_text = freeze.stdout if freeze.returncode == 0 else freeze.stderr
    (output_dir / "ci-freeze.txt").write_text(freeze_text or "", encoding="utf-8")


if __name__ == "__main__":
    main()
