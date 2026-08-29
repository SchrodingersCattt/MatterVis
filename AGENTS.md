# MatterVis — Agent / AI-Coding Guidelines

Conventions and constraints for AI coding agents *modifying* this repo.
Design rationale and implementation details live in source-code docstrings;
read the relevant module before modifying it.

> **For AI agents that want to *call* MatterVis** (not modify it),
> see [Caller API Contracts](docs/agents/) instead.

---

## Repository Layout

```
mat_viewer/
  app/                 Dash layout, callbacks, ViewerBackend internals
  api/                 REST + WebSocket blueprints
  render/              Plotly viewport / traces / cache internals
  structure/           CIF parsing, bonds, formula units, MolCrysKit bridge
  loader/              CIF/fragment bundle loading facade + upload helpers
  scene/               cell/cluster scene builder facade
  transforms/          repeat/grow/slab transform primitives and pipeline
  topology/            coordination-shell extraction & shape analysis
  style/               atom/bond rules, palette, disorder render helpers
  cube/                cube/orbital I/O, meshes, traces, export facade
  ortep/               thermal ellipsoid math, billboards, traces
  compass/             camera-projected lattice direction annotations
  renderer/            public Plotly facade for atoms, bonds, hulls, axes
  presets/             preset / style / catalog IO
  scenes/              tab/session scene state
  perf_log/            lightweight server event log facade
  depth_sort/          matplotlib depth-order helpers
  viewer_backend/      public ViewerBackend compatibility facade
  assets/              Dash CSS + JS for the panel layout
  static_publication/  matplotlib publication exporter
  tui/                 terminal viewer (Textual-based)
scripts/               runnable demo scripts
docs/                  human docs, caller API contracts, showcase images
tests/                 pytest regression suite
paper/                 figure-generation scripts for publications
```

---

## Testing Rules

- **Full suite must stay green.** Run
  `python -m pytest tests/ -n 4 --dist loadfile` before pushing.
- For a quick local signal, run
  `python -m pytest tests/ -n 4 --dist loadfile -m "not integration and not slow"`.
- Profile suite regressions by appending
  `--durations=50 --durations-min=0.25` to the full command.
- Lint with `ruff check mat_viewer/`.
- Example regressions: run the relevant `python scripts/<n>_*.py` and
  inspect the generated artifact under `scripts/_outputs/`.
- `pytest.ini` sets `timeout = 60` with `timeout_method = thread` for
  C-extension-safe timeout handling.

---

## Changing Code

- **Read the module docstring first.** Most modules document their design
  constraints in the module or class docstring. Respect those constraints.
- **Prefer additive changes.** Extend existing code paths rather than
  rewriting core logic; the regression suite is brittle to rewrites.
- **No magic numbers.** Put thresholds in config modules; reuse existing
  constants before adding new ones.
- **Reuse before reinventing.** Check existing modules and utilities —
  and upstream `molcrys_kit` — before writing new logic.
- **Do not hardcode version strings.** Version is derived from git tags
  via `setuptools_scm` → `mat_viewer/_version.py` (gitignored).
- **Name modules for the data/operation**, not a journal, project, or
  one-off output.
- **Add new capability at the lowest layer that fits**, then wrap upward.
- **Expose style choices as kwargs/state fields** instead of constants.
- **Visually verify rendering changes.** Static export can fail silently;
  regenerate the relevant script output and inspect it.

---

## Non-negotiable Design Principles

1. **Separate caller style from library capability.** No journal, project,
   or paper-specific naming under `mat_viewer/`. Defaults are conveniences;
   styling must be caller-overridable.
2. **Keep APIs layered.** Prefer pure primitives at the bottom, composable
   builders above them, and convenience wrappers at the top. Callers must be
   able to drop down a layer without reimplementing math.
3. **Do not mutate shared module state.** Exposed palettes/configs are
   readable defaults. Overrides must flow through explicit call/state data.
4. **Reuse before reinventing.** Before adding geometry, chemistry, topology,
   PBC, shape, disorder, or slab logic, check upstream `molcrys_kit` and
   existing MatterVis modules for the current primitive.
5. **Respect ownership boundaries.** Browser code stays in `frontend/`,
   service code in `api/` or `app/`, reusable library code in `mat_viewer/`,
   and chemistry semantics in `molcrys_kit`.
6. **Keep operation paths distinct.** Source operations work on real crystal
   objects and return through the loader. Display operations work on manifested
   scene data and must not pretend to create source objects.
7. **Keep math domain-neutral.** Projection, rotation, ellipsoid, and PBC
   primitives may live in math; coordination, labels, covalent radii, fragments,
   and species semantics do not.
8. **Separate state, assembly, and snapshots.** Editable scene state, render
   assembly, manual overlays, and scene-to-source snapshots have different
   owners; do not blur them for convenience.
9. **Keep interactive work asynchronous.** Dash/Flask request callbacks must
   not run expensive topology or figure assembly inline. Use the established
   worker/intent/state flow.

---

## MolCrysKit Boundary

`molcrys_kit` owns chemistry: molecule grouping, PBC unwrapping, packing
shells, shape classification, disorder resolution, slabs, and related
provenance. MatterVis owns loading adapters, scene state, rendering, UI,
and service surfaces.

When upstream already computes a chemistry quantity, consume it as the
single source of truth. Do not re-derive it locally "for safety". When
upstream deprecates a primitive, read the replacement docs and update the
public contract in `docs/agents/` if the payload changes.

---

## Documentation Maintenance

- When adding or changing a **public API** function or class, update the
  matching file in `docs/agents/`.
- When adding a new **user-facing feature**, add it to the Capability Map
  in `docs/agents/README.md`.
- When adding or changing a CLI subcommand, update `docs/cli.md`.
- Put deep implementation warnings in `docs/dev-notes.md` or local code
  comments, not in this top-level contract.
- Update examples and tests when behavior changes.

---

## Versioning & Release

Version is owned by **`setuptools_scm`** from the latest `vX.Y.Z` git tag.

Release steps:
1. Branch off `main`, create a version tag, push.
2. Tag push triggers CI (tests, build, publish).
3. `setuptools_scm` reads the tag at build time; no manual version file.

CI checkouts use `fetch-depth: 0` so `setuptools_scm` sees full tag history;
do not lower it.
