from __future__ import annotations

import json
from pathlib import Path

import pytest

from mat_viewer.cli import main

ROOT = Path(__file__).resolve().parents[2]
PUBLICATION_DOCS = (
    ROOT / "docs/agents/static_publication.md",
    ROOT / "paper/coordination/README.md",
    ROOT / "skills/visualize-materials/references/publication-layout.md",
)


@pytest.mark.parametrize("path", PUBLICATION_DOCS, ids=lambda path: path.name)
def test_publication_docs_use_single_view_cpu_contract(path: Path) -> None:
    document = path.read_text(encoding="utf-8")

    assert "--backend cpu" in document
    assert "external" in document.lower() or "separate" in document.lower()
    assert "--publication-preset dense_coordination" not in document
    assert "--publication-style blender" not in document
    assert "--publication-option PATH=VALUE" not in document


@pytest.mark.parametrize(
    "legacy_args",
    [
        ["--publication-layout"],
        ["--publication-preset", "dense_coordination"],
        ["--publication-style", "blender"],
        ["--publication-option", "materials.alpha=0.5"],
        ["--title", "legacy heading"],
        ["--subtitle", "legacy subtitle"],
    ],
)
def test_agent_cli_rejects_legacy_publication_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    legacy_args: list[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "render",
                str(tmp_path / "not-loaded.cif"),
                "-o",
                str(tmp_path / "not-created.svg"),
                *legacy_args,
                "--check",
                "--json",
            ]
        )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert raised.value.code == 2
    assert "unsupported by the backend-neutral render command" in payload["error"]
    assert not (tmp_path / "not-created.svg").exists()
