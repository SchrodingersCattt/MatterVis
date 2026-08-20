from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI
    import tomli as tomllib

from mat_viewer.capabilities import (
    CAPABILITY_REGISTRY,
    MOLCRYSKIT_CONTRACT_SHA,
    MOLCRYSKIT_MINIMUM,
    install_command,
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


def test_skill_has_no_second_installer_or_obsolete_package_entrypoint() -> None:
    assert not (SKILL / "scripts" / "install_runtime.sh").exists()
    text = "\n".join(path.read_text(encoding="utf-8") for path in SKILL.rglob("*.md"))
    assert "matter-vis==0.0.3" not in text
    assert "python -m crystal_viewer" not in text
    assert "matplotlib-flat-ortep.md" not in text
    assert "apt-get install" not in text


def test_unreleased_molcryskit_contract_pin_is_consistent() -> None:
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
        assert f"MOLCRYSKIT_CONTRACT_SHA: {MOLCRYSKIT_CONTRACT_SHA}" in text
        assert 'python -m pip install -e ".[all,test]"' in text

    development = (SKILL / "references" / "capabilities-and-install.md").read_text(
        encoding="utf-8"
    )
    assert MOLCRYSKIT_CONTRACT_SHA in development
