## Summary

<!-- What changed and why. -->

## How to verify

<!-- Commands, checks, or manual steps a reviewer can repeat. -->

## Documentation

In-scope changes update stable docs in this pull request. The review bar is in CONTRIBUTING.md.

<!--
Out of scope (pure refactors, test/CI-only, private helpers, docs-only
pull requests, or a MolCrysKit pin with no MatterVis behavior change):
add the exemption token and a one-line reason on the docs-paths line.
The token is Docs: N/A
The warn-only check ignores HTML comments and the unfilled label below.
Leaving that label blank does not count as the exemption.
-->

- Docs paths touched, or `Docs: N/A` + one-line reason:
- Changes render behavior, CLI, WebGL session/upload contracts, architecture, or user-visible display policy: yes / no
- `docs/cli.md` updated where relevant: yes / no
- `docs/architecture.md` or `docs/architecture/` updated where relevant: yes / no
- `docs/agents/` or `AGENTS.md` updated where relevant: yes / no
- MolCrysKit pin note:
- [ ] No chemistry heuristics (ionic/halide whitelists, `is_ionic_solid` gates, or similar) were added to or documented in the renderer.
