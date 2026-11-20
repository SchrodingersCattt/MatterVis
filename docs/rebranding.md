# Atomaestro name and package migration

**Current status:** The GitHub repository is **[Atomaestro](https://github.com/SchrodingersCattt/Atomaestro)** (formerly MatterVis). The software distributed through PyPI and the current CLI have **not yet been renamed**. Do not substitute proposed package or executable names into working installations until a release announces them.

| Surface | Available now | Planned, not yet released |
|---|---|---|
| GitHub | [`SchrodingersCattt/Atomaestro`](https://github.com/SchrodingersCattt/Atomaestro) | No further repository move is planned |
| PyPI distribution | [`matter-vis`](https://pypi.org/project/matter-vis/) | A new `atomaestro` distribution, subject to acceptance by PyPI |
| CLI | `mat-vis` | A primary `atomaestro` command, retaining `mat-vis` as a compatibility alias |
| Python imports | `mat_viewer` | Keep existing imports working; any new top-level import needs its own compatibility plan |
| Agent Skills and APIs | Existing `mat-vis`, `mat_viewer`, and `mattervis.*` contracts | Migrate presentation names without silently breaking stable machine-facing contracts |

## Install the current release

```bash
python -m pip install matter-vis
mat-vis --help
```

For a source checkout:

```bash
git clone https://github.com/SchrodingersCattt/Atomaestro.git
cd Atomaestro
python -m pip install -e .
```

Existing clones should update their remote to the canonical address:

```bash
git remote set-url origin https://github.com/SchrodingersCattt/Atomaestro.git
```

GitHub generally redirects old repository and issue links following a rename, as long as the old name is not reused. Scripts, documentation, integrations, Actions, and Git remotes should nevertheless be updated to the canonical URL. The old PyPI project URL **does not** redirect: it must remain available separately for existing installations.

## Next release: compatibility rules

The planned transition is tracked in [issue #316](https://github.com/SchrodingersCattt/Atomaestro/issues/316). Before switching the published distribution, verify that:

- The new PyPI project can be registered, installed and upgraded cleanly.
- The previous `matter-vis` distribution can be shipped as a **compatibility-only package** depending on the new distribution, without duplicating the `mat_viewer` package files.
- The `mat-vis` entry point and existing Python imports keep working, including in automation and JSON-mode CLI calls.
- Documentation, examples, CI, Agent Skills and dependent applications reflect the correct installed versions.
- Release notes distinguish the actual released interfaces from planned ones.

This page documents branding and packaging. It does not imply that planned multi-structure composition, Studio, SaaS or other roadmap capabilities are implemented in the current release.
