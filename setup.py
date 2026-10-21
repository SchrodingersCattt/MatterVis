"""Setuptools hook that copies the canonical skills tree into the wheel.

The copy lands in the build directory after the normal package build. It does
not write into the source tree, so an interrupted build cannot leave a partial
``mat_viewer/_bundled_skills`` that the source checkout would prefer over
``skills/``. Wheels cannot store symlinks: ``bdist_wheel`` would follow one and
package the target's bytes. The copy therefore refuses any symlink in a skill
directory instead of turning an outside path into an installed file.
"""

from pathlib import Path
from shutil import copytree, rmtree

from setuptools import setup
from setuptools.command.build_py import build_py


def _reject_skill_symlinks(source: Path) -> None:
    """Fail the build when a skill contains a symlink.

    ``bdist_wheel`` stores the target's bytes, so an outside link would become
    a normal file that ``show --file`` could serve.
    """
    if source.is_symlink():
        raise RuntimeError(f"refusing to bundle skill symlink: {source}")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"refusing to bundle skill symlink: {path}")


def copy_bundled_skills(source_root: Path, destination: Path) -> list[Path]:
    """Copy each canonical skill directory into ``destination``.

    Returns the file paths that were written. A symlink skill root, or any
    symlink inside a skill, raises ``RuntimeError`` before the copy starts.
    """
    written: list[Path] = []
    if not source_root.is_dir():
        return written
    skills = [
        source
        for source in sorted(source_root.iterdir(), key=lambda item: item.name)
        if source.is_dir() and (source / "SKILL.md").is_file()
    ]
    for source in skills:
        _reject_skill_symlinks(source)
    destination.mkdir(parents=True, exist_ok=True)
    for source in skills:
        target = destination / source.name
        copytree(source, target, dirs_exist_ok=True)
        for path in sorted(target.rglob("*")):
            if path.is_file() and not path.is_symlink():
                written.append(path)
    _prune_bytecode(destination)
    return written


def _prune_bytecode(root: Path) -> None:
    """Drop stray interpreter caches copied from a dirty source tree."""
    if not root.is_dir():
        return
    for cache in [path for path in root.rglob("__pycache__") if path.is_dir()]:
        rmtree(cache)
    for suffix in ("*.pyc", "*.pyo"):
        for path in root.rglob(suffix):
            if path.is_file() and not path.is_symlink():
                path.unlink()


class BuildBundledSkills(build_py):
    def run(self):
        super().run()
        source_root = Path(__file__).resolve().parent / "skills"
        destination = Path(self.build_lib) / "mat_viewer" / "_bundled_skills"
        copy_bundled_skills(source_root, destination)

    def get_outputs(self, include_bytecode=1):
        outputs = super().get_outputs(include_bytecode)
        root = Path(self.build_lib) / "mat_viewer" / "_bundled_skills"
        if not root.is_dir():
            return outputs
        seen = set(outputs)
        for path in sorted(root.rglob("*")):
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if not (path.is_symlink() or path.is_file()):
                continue
            text = str(path)
            if text not in seen:
                outputs.append(text)
                seen.add(text)
        return outputs


setup(cmdclass={"build_py": BuildBundledSkills})
