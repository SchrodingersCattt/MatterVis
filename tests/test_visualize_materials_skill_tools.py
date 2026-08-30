from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI
    import tomli as tomllib

from mat_viewer.capabilities import (
    CAPABILITY_REGISTRY,
    MOLCRYSKIT_INSTALL,
    MOLCRYSKIT_MINIMUM,
    install_command,
    resolve_requirements,
)

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "visualize-materials"


def _dependency_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].strip().lower()


def test_pyproject_extras_match_the_capability_registry() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    base = {_dependency_name(value) for value in project["dependencies"]}
    extras = project["optional-dependencies"]

    assert set(CAPABILITY_REGISTRY["core"].packages) == base
    for name, spec in CAPABILITY_REGISTRY.items():
        if name == "core":
            continue
        declared = {_dependency_name(value) for value in extras[spec.extra]}
        assert set(spec.packages) == declared

    optional_runtime = {
        package
        for spec in CAPABILITY_REGISTRY.values()
        if spec.extra is not None
        for package in spec.packages
    }
    assert optional_runtime == {_dependency_name(value) for value in extras["all"]}


def test_skill_install_matrix_is_generated_from_the_same_names_and_commands() -> None:
    document = (SKILL / "references" / "capabilities-and-install.md").read_text(
        encoding="utf-8"
    )
    start = document.index("<!-- capability-matrix:start -->")
    end = document.index("<!-- capability-matrix:end -->")
    rows = {}
    for line in document[start:end].splitlines():
        match = re.match(r"\|.+\| `([^`]+)` \| `([^`]+)` \|$", line)
        if match:
            rows[match.group(1)] = match.group(2)

    assert set(rows) == set(CAPABILITY_REGISTRY)
    for name, spec in CAPABILITY_REGISTRY.items():
        assert rows[name] == install_command((spec.extra,) if spec.extra else ())

    for alias in ("web-screenshot", "static-web-export"):
        assert alias in document
        assert resolve_requirements(alias).install_command in document


def test_skill_has_no_second_installer_or_obsolete_package_entrypoint() -> None:
    assert not (SKILL / "scripts" / "install_runtime.sh").exists()
    text = "\n".join(path.read_text(encoding="utf-8") for path in SKILL.rglob("*.md"))
    assert "matter-vis==0.0.3" not in text
    assert "python -m crystal_viewer" not in text
    assert "matplotlib-flat-ortep.md" not in text
    assert "apt-get install" not in text


def test_skill_routes_publication_and_multi_structure_guidance() -> None:
    document = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert (
        'description: "Use before drawing, showing, comparing, or animating atomistic'
        in document
    )
    assert len(document.splitlines()) <= 60
    assert "--check" not in document
    assert "--vector-overlays" not in document
    assert "open-ended retry loop" in document
    assert "do not run a separate analysis" in document
    assert "references/quickstart.md" in document
    assert "./references/publication-layout.md" in document
    assert "./references/multi-structure-panels.md" in document
    assert "./references/atom-property-coloring.md" in document
    assert document.index("MD trajectory") < document.index("per-atom scalar")
    assert "Read exactly one primary page" in document
    assert "Scene type wins" in document
    assert "Run the documented command before inspecting help" in document
    trajectory = (SKILL / "references" / "trajectory-animation.md").read_text(
        encoding="utf-8"
    )
    assert "--check" not in trajectory
    assert "--style ball_stick --show-cell --orthogonal" in trajectory
    assert "For nonperiodic or" in trajectory
    assert "Do not add `--view-direction` by default" in trajectory
    assert "largest" in trajectory and "lattice face" in trajectory
    assert "Use the input directly" in trajectory

    vectors = (SKILL / "references" / "vibration-mode-vectors.md").read_text(
        encoding="utf-8"
    )
    assert "--vector-overlays" in vectors
    assert "This page is complete for vibration rendering" in vectors
    assert "source-frame equilibrium atom coordinates" in vectors
    assert "handles synthetic-cell translation" in vectors
    polyhedra = (SKILL / "references" / "polyhedra.md").read_text(encoding="utf-8")
    assert len(polyhedra.splitlines()) <= 70
    assert '"level":"atom"' in polyhedra
    assert "--show-cell --show-axes" in polyhedra
    assert "every matching center" in polyhedra
    assert '"center_images":true' in polyhedra
    assert "`instance_overrides`" in polyhedra
    assert "Omit `cutoff` for the natural shell" in polyhedra
    assert "`effective_colors`" in polyhedra

    property_coloring = (SKILL / "references" / "atom-property-coloring.md").read_text(
        encoding="utf-8"
    )
    assert "mat-vis inspect INPUT --properties --json" in property_coloring
    assert "Never let `auto` assign tensor meaning" in property_coloring
    assert "Never rescale each frame independently" in property_coloring
    assert "CSV or NPZ" in property_coloring
    assert "`lut_hash`" in property_coloring
    assert "`manifest_hash`" in property_coloring


def test_released_molcryskit_minimum_is_consistent() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    molcryskit = next(
        dependency
        for dependency in data["project"]["dependencies"]
        if _dependency_name(dependency) == "molcrys-kit"
    )
    assert molcryskit == f"molcrys-kit>={MOLCRYSKIT_MINIMUM}"
    assert "git+" not in molcryskit

    for workflow in ("ci.yml", "release.yml"):
        text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert f'MOLCRYSKIT_MINIMUM: "{MOLCRYSKIT_MINIMUM}"' in text
        assert "molcrys-kit==${MOLCRYSKIT_MINIMUM}" in text
        assert 'python -m pip install -e ".[all,test]"' in text

    development = (SKILL / "references" / "capabilities-and-install.md").read_text(
        encoding="utf-8"
    )
    assert MOLCRYSKIT_INSTALL in development
