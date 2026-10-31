# Contributing to MatterVis

## Documentation policy

Every pull request that changes render behavior, the CLI, WebGL
session/upload contracts, architecture, or user-visible display policy
updates the stable documentation in the same pull request.

The pull request template (`.github/pull_request_template.md`) is the
process gate:

- **Summary** and **How to verify** state what changed and how a reviewer
  can check it.
- **Documentation** lists the docs paths touched, or `Docs: N/A` plus a
  one-line reason when the change is out of scope.
- Say whether the pull request changes render behavior, CLI, WebGL
  session/upload contracts, architecture, or user-visible display policy
  (yes/no).
- Say whether `docs/cli.md`, `docs/architecture.md` (or
  `docs/architecture/`), `docs/agents/`, or `AGENTS.md` were updated where
  relevant.
- **MolCrysKit pin note:** when the pin changes, name it. When it does
  not, write `N/A`.
- Confirm that no chemistry heuristics were added to or documented in the
  renderer.

`Docs: N/A` belongs on the docs-paths line together with the reason. The
template's unfilled label does not count. The warn-only check ignores
HTML comments and that unfilled label until a real `Docs: N/A` exemption
is present.

### In scope

Update the matching stable doc in the same pull request:

- CLI / flags → `docs/cli.md`
- Architecture, session isolation, upload, and worker contracts →
  `docs/architecture.md` or `docs/architecture/`
- Display modes, bond graph / radius policy, and selection verification
- Perf, scores, and QA notes when those contracts change
- `docs/agents/` or `AGENTS.md` when a public contract or standing agent
  rule changes

Prefer contract tests or goldens where they exist. Use prose for the rest.

### Out of scope

Write `Docs: N/A` and a one-line reason:

- Pure refactors, test/CI-only changes, private helpers, and docs-only
  pull requests
- Pinning MolCrysKit without a MatterVis behavior change (note the pin in
  the pull request body; update docs only if MatterVis contract text
  mentions the pin)

### Review bar

Missing documentation on an in-scope pull request is review **P1**.
`Docs: N/A` does not clear an in-scope change.

### Render interface

MatterVis is a pure render interface. Docs stay geometry, bond-graph, and
display-mode oriented. Chemistry documentation belongs in MolCrysKit.

Do not document or add chemistry heuristics (ionic/halide whitelists,
`is_ionic_solid` gates) in the renderer.

### Warn-only CI

On pull requests, CI warns when `mat_viewer/` (including the Dash and REST
server under `mat_viewer/app/` and `mat_viewer/api/`), `frontend/`, or a
top-level `server/` path changes, no `docs/**`, `AGENTS.md`, or
`CONTRIBUTING.md` file changes, and the pull request body has no
substantive `Docs: N/A`. The check does not fail the build. It does not
judge whether in-scope docs are substantively complete; that remains
review P1.

A green CI run does not merge a pull request. MatterVis merges need
explicit maintainer approval.
