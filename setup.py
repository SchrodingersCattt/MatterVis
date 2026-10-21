"""Setuptools hook that copies the canonical skills tree into the wheel.

The copy lands in the build directory after the normal package build. It does
not write into the source tree, so an interrupted build cannot leave a partial
``mat_viewer/_bundled_skills`` that the source checkout would prefer over
``skills/``. Symlinks are preserved rather than followed: a link that points
outside a skill stays a link and is rejected when the resource is read, the
same as a source checkout.
"""

from pathlib import Path
from shutil import copytree, rmtree

from setuptools import setup
from setuptools.command.build_py import build_py


def copy_bundled_skills(source_root: Path, destination: Path) -> list[Path]:
    """Copy each canonical skill directory into ``destination``.

    Skill roots that are themselves symlinks are skipped. Nested symlinks are
    copied as symlinks. Returns the file and symlink paths that were written.
    """
    written: list[Path] = []
    if not source_root.is_dir():
        return written
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_root.iterdir(), key=lambda item: item.name):
        if source.is_symlink() or not source.is_dir():
            continue
        if not (source / "SKILL.md").is_file():
            continue
        target = destination / source.name
        copytree(source, target, dirs_exist_ok=True, symlinks=True)
        for path in sorted(target.rglob("*")):
            if path.is_symlink() or path.is_file():
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
