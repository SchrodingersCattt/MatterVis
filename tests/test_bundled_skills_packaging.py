"""Build-time copy of the canonical skills tree."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _run_copy(source_root: Path, destination: Path) -> subprocess.CompletedProcess[str]:
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
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_copy_bundled_skills_includes_nested_files(tmp_path: Path) -> None:
    source_root = tmp_path / "skills"
    skill = source_root / "demo"
    (skill / "references" / "images").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
    (skill / "references" / "images" / "mark.png").write_bytes(b"png")
    (skill / "scripts" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    destination = tmp_path / "build" / "mat_viewer" / "_bundled_skills"

    result = _run_copy(source_root, destination)
    assert result.returncode == 0, result.stderr or result.stdout
    written = {Path(item) for item in json.loads(result.stdout)}
    nested = destination / "demo" / "references" / "images" / "mark.png"
    assert nested in written and nested.read_bytes() == b"png"
    assert destination / "demo" / "scripts" / "run.sh" in written
    assert not (ROOT / "mat_viewer" / "_bundled_skills" / "demo").exists()


def test_copy_bundled_skills_refuses_symlinks(tmp_path: Path) -> None:
    source_root = tmp_path / "skills"
    skill = source_root / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo\n---\n", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (skill / "linked.txt").symlink_to(outside)
    destination = tmp_path / "build" / "mat_viewer" / "_bundled_skills"

    result = _run_copy(source_root, destination)
    assert result.returncode != 0
    assert "refusing to bundle skill symlink" in result.stderr
    assert not (destination / "demo").exists()

    alias_root = tmp_path / "alias-skills"
    alias_root.mkdir()
    (alias_root / "alias").symlink_to(skill, target_is_directory=True)
    alias_dest = tmp_path / "alias-build"
    alias_result = _run_copy(alias_root, alias_dest)
    assert alias_result.returncode != 0
    assert "refusing to bundle skill symlink" in alias_result.stderr
    assert not (alias_dest / "alias").exists()
