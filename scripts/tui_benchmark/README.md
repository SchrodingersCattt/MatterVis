# TUI agent benchmark seed

This directory contains the repository-owned, synthetic seed for comparing
MatterVis terminal observations with images, raw CIF, ASE, pymatgen,
MolCrysKit, and the MatterVis `/api/v2` surface.

This seed is characterization only. It records the post-merge TUI behavior and
independent analytic oracles; it does not fix production defects or claim a
complete benchmark. Current known gaps are limited to narrowly named strict
xfails, including observation-scope naming and rotation-dependent viewport fit.

## Contents

- `fixtures/`: small analytic CIFs dedicated under CC0-1.0.
- `manifests/synthetic_oracles.v1.json`: independent answers, hashes, and
  layered benchmark identities.
- `manifests/task_eligibility.v1.json`: which arms may fairly attempt each
  seed task.
- `schemas/`: closed JSON Schema documents for identities, oracles, and task
  eligibility.
- `../tui_agent_audit.py`: reproducible measurements corresponding to the
  strict characterization tests.

## Identity namespaces

Benchmark IDs intentionally distinguish:

1. source CIF sites;
2. symmetry-expanded instances;
3. displayed periodic-image copies;
4. molecules;
5. fragments.

These are benchmark namespaces and mappings, not a declaration that current
MatterVis payload IDs are already stable public identifiers.

## Oracle policy

Synthetic answers are analytic and do not import `crystal_viewer.tui`.
Real structures such as DAP-4, SY, PEP, HPEP, and MPEP are challenge or
implementation-agreement cases until their provenance is resolved; they are
not the CI accuracy oracle.

## Environment status

The audit captures the current commit, package versions, direct-install
metadata, locale, Unicode version, terminal environment, and glyph widths.
This records an environment but does not make it immutable. Dependencies other
than the pinned MolCrysKit revision remain unlocked in the repository. The full
comparative benchmark must use a lockfile or immutable image.

## Run

```bash
PYTHONDONTWRITEBYTECODE=1 LC_ALL=C.UTF-8 \
  python scripts/tui_agent_audit.py
pytest -q tests/tui/
```

The expected result is passing manifest/current-regression tests plus strict
xfails only for remaining defects. An unexpected pass fails because each xfail
is strict; the PR that fixes a defect removes its corresponding marker.
