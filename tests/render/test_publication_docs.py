from __future__ import annotations

import argparse
from pathlib import Path
import re
import shlex

import pytest

from crystal_viewer.render.cli import (
    _build_render_parser,
    _parse_publication_options,
)
from crystal_viewer.render.publication import publication_config

ROOT = Path(__file__).resolve().parents[2]
CLI_DOCS = (
    ROOT / "docs/agents/static_publication.md",
    ROOT / "paper/coordination/README.md",
)


def _documented_command(path: Path) -> list[str]:
    blocks = re.findall(
        r"```bash\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL
    )
    assert len(blocks) == 1, f"{path} must contain one canonical bash example"
    command_text = blocks[0].replace(chr(92) + "\n", " ")
    command = shlex.split(command_text, comments=False, posix=True)
    assert command[:2] == ["mat-vis", "render"]
    assert "--config" not in command
    assert "python" not in command[:2]
    return command


@pytest.mark.parametrize("path", CLI_DOCS, ids=lambda path: path.name)
def test_publication_documentation_uses_live_cli_contract(path: Path) -> None:
    command = _documented_command(path)
    parser = argparse.ArgumentParser()
    _build_render_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(command[1:])

    assert args.command == "render"
    assert args.publication_layout is True
    assert args.publication_style == "blender"
    style = _parse_publication_options(
        args.publication_preset,
        args.publication_option,
        publication_style=args.publication_style,
        site_styles=args.publication_site_style,
        legend_entries=args.publication_legend_entry,
        panel_labels=args.publication_panel_label,
        legend_footer=args.publication_legend_footer,
    )
    assert publication_config(style)["materials"]


def test_skill_reference_points_to_parser_checked_documentation() -> None:
    skill_doc = (
        ROOT / "skills/visualize-materials/references/publication-layout.md"
    ).read_text(encoding="utf-8")

    assert "docs/agents/static_publication.md" in skill_doc
    assert CLI_DOCS[0].is_file()
