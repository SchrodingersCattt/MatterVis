from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_ci_badge_tracks_main_ci_workflow() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "actions/workflows/ci.yml/badge.svg?branch=main" in readme
    assert "actions/workflows/release.yml/badge.svg" not in readme


def test_github_release_creation_has_explicit_repository_context() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert 'gh release create "${GITHUB_REF_NAME}" dist/*' in workflow
    assert '--repo "${GITHUB_REPOSITORY}"' in workflow
