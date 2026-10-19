from __future__ import annotations

from pathlib import Path


MAT_VIEWER = Path(__file__).resolve().parents[1] / "mat_viewer"

HARD_LINE_LIMIT = 1000
RELAXED_LINE_LIMITS = {}
KNOWN_OVERSIZE_DURING_SPLIT = {
    # These modules were already over the hard cap during the domain split.
    # Keep the guard active while acknowledging the migration debt so unrelated
    # feature PRs can still run the full suite.
    # Display-default restoration and property-range resolution remain part
    # of the backend mixin until the ongoing app split moves them out.
    "app/backend_core.py": 1518,
    "app/factory.py": 1116,
    "loader/core.py": 1224,
    "cli.py": 1320,
    "ortep/core.py": 1146,
    # Existing CLI parser is just over the hard cap while its argument
    # groups are being split into the dedicated controls module.
    "render/cli.py": 1050,
    "render/planning.py": 1348,
    "structure/loader/core.py": 1050,
}
ALLOWED_TOP_LEVEL_PY = {
    "__init__.py",
    "__main__.py",
    "_version.py",
    "agent.py",
    "agent_topology.py",
    "cache_keys.py",
    "capabilities.py",
    "cli.py",
    "skill_viewer.py",
}


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_mat_viewer_modules_stay_small_enough() -> None:
    failures: list[str] = []
    for path in sorted(MAT_VIEWER.rglob("*.py")):
        rel_path = path.relative_to(MAT_VIEWER).as_posix()
        limit = (
            KNOWN_OVERSIZE_DURING_SPLIT.get(rel_path)
            or KNOWN_OVERSIZE_DURING_SPLIT.get(path.name)
            or RELAXED_LINE_LIMITS.get(rel_path)
            or RELAXED_LINE_LIMITS.get(path.name)
            or HARD_LINE_LIMIT
        )
        count = _line_count(path)
        if count > limit:
            failures.append(
                f"{path.relative_to(MAT_VIEWER.parent)} has {count} lines > {limit}"
            )

    assert not failures, "\n".join(failures)


def test_mat_viewer_top_level_stays_folder_first() -> None:
    top_level_py = {path.name for path in MAT_VIEWER.glob("*.py")}
    assert top_level_py <= ALLOWED_TOP_LEVEL_PY
