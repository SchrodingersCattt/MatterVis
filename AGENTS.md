# MatterVis — Developer Contract

This file is for agents and humans **modifying** MatterVis. It is the
top-level contract only; module/API details belong in linked docs.
Keep this file short, architectural, and free of function-specific rules.

## Read the right document first

- Caller-facing API contracts: [`agents/README.md`](agents/README.md).
- Detailed feature contracts: `agents/*_api.md` and
  [`agents/dash_service.md`](agents/dash_service.md).
- Implementation notes and historical traps:
  [`docs/dev-notes.md`](docs/dev-notes.md),
  [`docs/postmortems.md`](docs/postmortems.md).

If a change touches a public feature, read the matching `agents/` file
before editing. If a change touches chemistry delegation, disorder,
fragmentation, topology, slabs, or loader/render boundaries, also read
`docs/dev-notes.md`.

## Repository ownership

| Path | Owns |
| --- | --- |
| `crystal_viewer/` | Python library: structure, operations, analysis, render, scene, math, utilities |
| `api/` | REST/WebSocket service surface for scripts and agents |
| `app/` | Dash Python BFF, callbacks, layouts, `ViewerBackend` |
| `frontend/` | Browser-only JavaScript/CSS assets loaded by Dash |
| `agents/` | Public caller contracts and automation-facing API docs |
| `docs/` | Human docs, developer notes, postmortems, score tables |
| `scripts/` | Runnable examples and regression figure generators |
| `tests/` | Unit and integration tests |

New features should normally fit one of these owners. Avoid new
top-level modules unless the existing ownership model cannot stretch.

## Non-negotiable design principles

1. **Separate caller style from library capability.** No journal,
   project, or paper-specific naming under `crystal_viewer/`. Defaults
   are conveniences; styling must be caller-overridable.
2. **Keep APIs layered.** Prefer pure primitives at the bottom,
   composable builders above them, and convenience wrappers at the top.
   Callers must be able to drop down a layer without reimplementing math.
3. **Do not mutate shared module state.** Exposed palettes/configs are
   readable defaults. Overrides must flow through explicit call/state data.
4. **Reuse before reinventing.** Before adding geometry, chemistry,
   topology, PBC, shape, disorder, or slab logic, check upstream
   `molcrys_kit` and existing MatterVis modules for the current primitive.
5. **Respect ownership boundaries.** Browser code stays in `frontend/`,
   service code in `api/` or `app/`, reusable library code in
   `crystal_viewer/`, and chemistry semantics in `molcrys_kit`.
6. **Keep operation paths distinct.** Source operations work on real
   crystal objects and return through the loader. Display operations work
   on manifested scene data and must not pretend to create source objects.
7. **Keep math domain-neutral.** Projection, rotation, ellipsoid, and PBC
   primitives may live in math; coordination, labels, covalent radii,
   fragments, and species semantics do not.
8. **Separate state, assembly, and snapshots.** Editable scene state,
   render assembly, manual overlays, and scene-to-source snapshots have
   different owners; do not blur them for convenience.
9. **Keep interactive work asynchronous.** Dash/Flask request callbacks
   must not run expensive topology or figure assembly inline. Use the
   established worker/intent/state flow.
10. **Visually verify rendering changes.** Static export can fail
    silently; regenerate the relevant script output and inspect it.

## MolCrysKit boundary

`molcrys_kit` owns chemistry: molecule grouping, PBC unwrapping,
packing shells, shape classification, disorder resolution, slabs, and
related provenance. MatterVis owns loading adapters, scene state,
rendering, UI, and service surfaces.

When upstream already computes a chemistry quantity, consume it as the
single source of truth. Do not re-derive it locally “for safety”. When
upstream deprecates a primitive, read the replacement docs and update
the public contract in `agents/` if the payload changes.

## Public contracts

The caller-visible guarantees for cube rendering, compass overlays,
scene figures, ORTEP, atom/bond groups, transforms, polyhedra,
selection, config, REST, and WebSocket behavior live under `agents/`.
Breaking one of those contracts requires updating the matching doc;
back-incompatible changes require an API/version bump.

## Tests, lint, and examples

- Unit tests: `pytest tests/`.
- Lint: `ruff check crystal_viewer/`.
- Example regressions: run the relevant `python scripts/<n>_*.py` and
  inspect the generated artifact under `scripts/_outputs/`.

## Adding or changing code

- Name modules and symbols for the data/operation, not a journal,
  project, or one-off output.
- Add new capability at the lowest layer that fits, then wrap upward.
- Expose style choices as kwargs/state fields instead of constants.
- Update the matching `agents/` file for public API changes.
- Update examples and tests when behavior changes.
- Put deep implementation warnings in `docs/dev-notes.md` or local
  code comments, not in this top-level contract.
