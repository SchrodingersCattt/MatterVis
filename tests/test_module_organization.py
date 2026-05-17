from __future__ import annotations

from pathlib import Path


CRYSTAL_VIEWER = Path(__file__).resolve().parents[1] / "crystal_viewer"

HARD_LINE_LIMIT = 1000
RELAXED_LINE_LIMITS = {}
KNOWN_OVERSIZE_DURING_SPLIT = {}
ALLOWED_TOP_LEVEL_PY = {"__init__.py", "__main__.py"}


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_crystal_viewer_modules_stay_small_enough() -> None:
    failures: list[str] = []
    for path in sorted(CRYSTAL_VIEWER.rglob("*.py")):
        rel_path = path.relative_to(CRYSTAL_VIEWER).as_posix()
        limit = (
            KNOWN_OVERSIZE_DURING_SPLIT.get(rel_path)
            or KNOWN_OVERSIZE_DURING_SPLIT.get(path.name)
            or RELAXED_LINE_LIMITS.get(rel_path)
            or RELAXED_LINE_LIMITS.get(path.name)
            or HARD_LINE_LIMIT
        )
        count = _line_count(path)
        if count > limit:
            failures.append(f"{path.relative_to(CRYSTAL_VIEWER.parent)} has {count} lines > {limit}")

    assert not failures, "\n".join(failures)


def test_crystal_viewer_top_level_stays_folder_first() -> None:
    top_level_py = {path.name for path in CRYSTAL_VIEWER.glob("*.py")}
    assert top_level_py <= ALLOWED_TOP_LEVEL_PY
