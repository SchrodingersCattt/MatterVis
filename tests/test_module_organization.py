from __future__ import annotations

from pathlib import Path


CRYSTAL_VIEWER = Path(__file__).resolve().parents[1] / "crystal_viewer"

HARD_LINE_LIMIT = 1000
RELAXED_LINE_LIMITS = {
    # These modules are mature, cohesive entry points that are just over the
    # general cap. Keep them visible so future growth is intentional.
    "loader.py": 1200,
    "transforms.py": 1200,
}
KNOWN_OVERSIZE_DURING_SPLIT = {
    # Commit 2 removes this exception.
    "dash_app_impl.py": 7600,
    # Commit 3 removes this exception.
    "renderer_scene_traces.py": 2700,
}


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_crystal_viewer_modules_stay_small_enough() -> None:
    failures: list[str] = []
    for path in sorted(CRYSTAL_VIEWER.glob("*.py")):
        limit = (
            KNOWN_OVERSIZE_DURING_SPLIT.get(path.name)
            or RELAXED_LINE_LIMITS.get(path.name)
            or HARD_LINE_LIMIT
        )
        count = _line_count(path)
        if count > limit:
            failures.append(f"{path.relative_to(CRYSTAL_VIEWER.parent)} has {count} lines > {limit}")

    assert not failures, "\n".join(failures)
