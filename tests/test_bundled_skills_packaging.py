"""Build-time copy of the canonical skills tree."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_copy_bundled_skills_preserves_symlinks_and_nested_files(tmp_path: Path) -> None:
    source_root = tmp_path / "skills"
    skill = source_root / "demo"
    (skill / "references" / "images").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
    (skill / "references" / "images" / "mark.png").write_bytes(b"png")
    (skill / "scripts" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (skill / "linked.txt").symlink_to(outside)
    alias = source_root / "alias"
    alias.symlink_to(skill, target_is_directory=True)
    destination = tmp_path / "build" / "mat_viewer" / "_bundled_skills"

    script = r"""
import json
import setuptools
from pathlib import Path
import runpy
setuptools.setup = lambda *args, **kwargs: None
ns = runpy.run_path(%r)
written = ns["copy_bundled_skills"](Path(%r), Path(%r))
print(json.dumps([str(path) for path in written]))
""" % (str(ROOT / "setup.py"), str(source_root), str(destination))
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    written = {Path(item) for item in json.loads(result.stdout)}
    nested = destination / "demo" / "references" / "images" / "mark.png"
    script_path = destination / "demo" / "scripts" / "run.sh"
    link = destination / "demo" / "linked.txt"
    assert nested in written and nested.read_bytes() == b"png"
    assert script_path in written
    assert link.is_symlink()
    assert link.resolve() == outside.resolve()
    assert not (destination / "alias").exists()
    assert not (ROOT / "mat_viewer" / "_bundled_skills" / "demo").exists()
