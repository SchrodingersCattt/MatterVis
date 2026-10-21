from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from mat_viewer.cli import main
from mat_viewer import skill_viewer


ROOT = Path(__file__).resolve().parents[1]


def test_skills_list_does_not_import_numeric_or_render_stack() -> None:
    script = """
import sys
from mat_viewer.cli import main
main(["skills", "list"])
loaded = [name for name in ("numpy", "ase", "mat_viewer.render") if name in sys.modules]
if loaded:
    raise SystemExit("skills imported " + ", ".join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_skills_list_json_is_stable(capsys: pytest.CaptureFixture[str]) -> None:
    main(["skills", "list", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "mattervis.skills.list/v1"
    assert [item["name"] for item in payload["skills"]] == [
        "inspect-structure-tui",
        "visualize-materials",
    ]
    assert all(item["description"] for item in payload["skills"])


def test_skills_show_default_is_the_raw_entrypoint(capsys: pytest.CaptureFixture[str]) -> None:
    main(["skills", "show", "visualize-materials"])
    assert capsys.readouterr().out == (
        ROOT / "skills/visualize-materials/SKILL.md"
    ).read_text(encoding="utf-8")


def test_skills_show_file_and_tree_are_progressive(capsys: pytest.CaptureFixture[str]) -> None:
    main(["skills", "show", "visualize-materials", "--file", "references/camera.md"])
    assert "# Camera Contract" in capsys.readouterr().out

    main(["skills", "tree", "visualize-materials", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "mattervis.skills.tree/v1"
    assert payload["resources"][0]["path"] == "SKILL.md"
    assert all("content" not in resource for resource in payload["resources"])


def test_skills_reject_escape_and_broken_resources(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["skills", "show", "visualize-materials", "--file", "../SKILL.md"])
    assert exc_info.value.code == 2
    assert "escapes" in capsys.readouterr().err


def test_skill_links_are_nested_deduplicated_and_cycle_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "skills"
    skill_root = root / "demo"
    (skill_root / "references" / "nested").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo\n---\n"
        "[one](references/one.md#part) [again](references/one.md) "
        "[script](scripts/tool.py)\n",
        encoding="utf-8",
    )
    (skill_root / "references/one.md").write_text(
        "[nested](nested/two.md) [cycle](../SKILL.md)\n", encoding="utf-8"
    )
    (skill_root / "references/nested/two.md").write_text("# Nested\n", encoding="utf-8")
    (skill_root / "scripts").mkdir()
    (skill_root / "scripts/tool.py").write_text("raise SystemExit\n", encoding="utf-8")
    monkeypatch.setattr(skill_viewer, "_skills_root", lambda: root)

    skill = skill_viewer.get_skill("demo")
    rows = skill_viewer.tree(skill)
    assert [row["path"] for row in rows] == [
        "SKILL.md",
        "references/one.md",
        "references/nested/two.md",
        "scripts/tool.py",
    ]
    documents = skill_viewer.all_documents(skill)
    assert [document["path"] for document in documents] == [
        "SKILL.md",
        "references/one.md",
        "references/nested/two.md",
    ]


def test_skill_tree_rejects_broken_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "skills"
    skill_root = root / "demo"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("[missing](references/nope.md)\n", encoding="utf-8")
    monkeypatch.setattr(skill_viewer, "_skills_root", lambda: root)
    with pytest.raises(skill_viewer.SkillError, match="resource not found"):
        skill_viewer.tree(skill_viewer.get_skill("demo"))


def _install_demo_skill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    root = tmp_path / "skills"
    skill_root = root / "demo"
    (skill_root / "references").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(skill_viewer, "_skills_root", lambda: root)


def test_show_prints_broken_links_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = "hello\n[missing](references/nope.md)\n"
    _install_demo_skill(monkeypatch, tmp_path, text)
    with pytest.raises(SystemExit) as exc_info:
        main(["skills", "show", "demo"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == text
    assert "Error:" in captured.err
    assert "resource not found" in captured.err

    with pytest.raises(SystemExit) as exc_info:
        main(["skills", "show", "demo", "--json"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema"] == "mattervis.skills.show/v1"
    assert payload["content"] == text
    assert "Error:" in captured.err


def test_show_ignores_code_spans_and_directory_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (
        "hello\n"
        "```\n[label](path/to/your-file.md)\n```\n"
        "`[inline](missing.md)`\n"
        "[refs](references/)\n"
        "[page](references/page.md)\n"
    )
    _install_demo_skill(monkeypatch, tmp_path, text)
    (tmp_path / "skills" / "demo" / "references" / "page.md").write_text("# Page\n", encoding="utf-8")
    main(["skills", "show", "demo"])
    assert capsys.readouterr().out == text

    main(["skills", "tree", "demo", "--json"])
    payload = json.loads(capsys.readouterr().out)
    links = payload["resources"][0]["links"]
    assert [link["target"] for link in links] == ["references", "references/page.md"]
    assert links[0]["kind"] == "directory"
    assert [resource["path"] for resource in payload["resources"]] == [
        "SKILL.md",
        "references/page.md",
    ]

    main(["skills", "show", "demo", "--all"])
    expanded = capsys.readouterr().out
    assert "path/to/your-file.md" in expanded
    assert "# Page" in expanded


def test_skills_error_json_contract(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["skills", "show", "nope", "--json"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("Error:")
    payload = json.loads(captured.out)
    assert payload["schema"] == "mattervis.error/v1"
    assert payload["ok"] is False
    assert isinstance(payload["error"], str) and payload["error"]
