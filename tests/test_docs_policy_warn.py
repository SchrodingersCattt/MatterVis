"""The docs-policy check warns and always exits 0."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "docs_policy_warn.py"
TEMPLATE = ROOT / ".github" / "pull_request_template.md"


def _load():
    spec = importlib.util.spec_from_file_location("docs_policy_warn", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = _load()


def _run(monkeypatch, capsys, paths: str, body: str) -> tuple[int, str]:
    monkeypatch.setenv("DOCS_POLICY_PATHS", paths)
    monkeypatch.delenv("DOCS_POLICY_BASE_REF", raising=False)
    monkeypatch.setenv("PR_BODY", body)
    code = POLICY.main()
    return code, capsys.readouterr().out


def test_code_without_docs_or_exemption_warns_and_exits_zero(monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "mat_viewer/api/ws.py\nfrontend/assets/mattervis.js", "")
    assert code == 0
    assert "::warning::" in out
    assert "does not fail the build" in out


def test_server_tree_and_frontend_are_code_paths(monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "server/app.py", "")
    assert code == 0
    assert "::warning::" in out


def test_docs_file_suppresses_the_warning(monkeypatch, capsys) -> None:
    code, out = _run(
        monkeypatch,
        capsys,
        "mat_viewer/cli.py\ndocs/cli.md\nAGENTS.md",
        "",
    )
    assert code == 0
    assert "::warning::" not in out
    assert "documentation files changed" in out


def test_contributing_counts_as_docs(monkeypatch, capsys) -> None:
    code, out = _run(monkeypatch, capsys, "frontend/assets/mattervis.js\nCONTRIBUTING.md", "")
    assert code == 0
    assert "::warning::" not in out


def test_visible_exemption_suppresses_the_warning(monkeypatch, capsys) -> None:
    code, out = _run(
        monkeypatch,
        capsys,
        "mat_viewer/app/serve_cli.py",
        "Docs: N/A — test-only helper moved inside the server package",
    )
    assert code == 0
    assert "::warning::" not in out
    assert "Docs: N/A" in out


def test_html_comment_does_not_count_as_exemption(monkeypatch, capsys) -> None:
    code, out = _run(
        monkeypatch,
        capsys,
        "mat_viewer/cli.py",
        "<!-- Docs: N/A — this is only an instruction -->\nNo exemption here.\n",
    )
    assert code == 0
    assert "::warning::" in out


def test_tests_and_workflow_only_changes_are_skipped(monkeypatch, capsys) -> None:
    code, out = _run(
        monkeypatch,
        capsys,
        "tests/test_docs_policy_warn.py\n.github/workflows/ci.yml",
        "",
    )
    assert code == 0
    assert "::warning::" not in out
    assert "skipped" in out


def test_unfilled_template_does_not_self_satisfy(monkeypatch, capsys) -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    assert POLICY.UNFILLED_DOCS_LABEL in template
    assert "Docs: N/A" not in POLICY.substantive_body(template)
    code, out = _run(monkeypatch, capsys, "mat_viewer/cli.py", template)
    assert code == 0
    assert "::warning::" in out


def test_exemption_added_to_the_template_suppresses_the_warning(monkeypatch, capsys) -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    body = template.replace(
        POLICY.UNFILLED_DOCS_LABEL,
        POLICY.UNFILLED_DOCS_LABEL + " Docs: N/A — pure refactor of a private helper",
        1,
    )
    code, out = _run(monkeypatch, capsys, "frontend/assets/mattervis.js", body)
    assert code == 0
    assert "::warning::" not in out


def test_rename_status_keeps_both_paths() -> None:
    blob = b"M\0mat_viewer/cli.py\0R100\0mat_viewer/old.py\0docs/new.md\0"
    paths = POLICY.paths_from_name_status_z(blob)
    assert paths == ["mat_viewer/cli.py", "mat_viewer/old.py", "docs/new.md"]
    assert POLICY.evaluate(paths, "") is None


def test_rename_inside_code_still_warns() -> None:
    blob = b"R090\0mat_viewer/a.py\0mat_viewer/b.py\0"
    paths = POLICY.paths_from_name_status_z(blob)
    assert POLICY.evaluate(paths, "") == POLICY.WARNING


def test_git_failure_warns_and_exits_zero(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DOCS_POLICY_PATHS", raising=False)
    monkeypatch.delenv("DOCS_POLICY_BASE_REF", raising=False)
    monkeypatch.setenv("DOCS_POLICY_BASE", "this-ref-does-not-exist")
    monkeypatch.setenv("PR_BODY", "")
    assert POLICY.main() == 0
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "does not fail the build" in out


def test_workflow_job_cannot_fail_the_build() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    start = workflow.index("  docs-policy-warn:\n")
    block = workflow[start:]
    assert "continue-on-error: true" in block
    assert "github.event_name == 'pull_request'" in block
    assert "docs_policy_warn.py" in block


def test_policy_docs_state_the_review_bar() -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")

    for text in (contributing, agents):
        assert "review **P1**" in text or "review P1" in text
        assert "Docs: N/A" in text
        assert "pure render interface" in text
        assert "MolCrysKit" in text
    assert "CONTRIBUTING.md" in agents
    assert "pure render interface" in architecture
    assert "CONTRIBUTING.md" in architecture
    assert "## Summary" in template
    assert "## How to verify" in template
    assert "## Documentation" in template
    assert "MolCrysKit pin note:" in template
    assert "is_ionic_solid" in template
    assert "yes / no" in template
