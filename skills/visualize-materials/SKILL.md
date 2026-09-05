---
name: visualize-materials
description: "Render atomistic structures with MatterVis. Use before any structure drawing, preview, comparison, coordination/polyhedron view, vibration, trajectory, or animation."
---

# Visualize Materials

Use the public `mat-vis` CLI for coordinate-based atomistic visuals. Do not
replace it with ASE plotting, raw matplotlib, or private MatterVis imports.

## Default path

1. Inspect once: `mat-vis inspect INPUT --json`.
2. Render one artifact with the base CPU backend:

```bash
mat-vis render INPUT -o OUTPUT.png --backend cpu --json \
  --orthogonal --background '#FFFFFF'
```

Auto mode shows the cell for periodic inputs and hides synthetic or nonperiodic
cells. Keep the automatic camera unless the requested evidence needs a specific
direction. Read the render JSON and inspect the final-size artifact before
delivery.

## Route once

Read exactly one primary reference: use the first matching line.
- Atom-centred forces with density fields: [force vectors](./references/atomic-force-vectors.md).

- Vibration arrows: [vibration vectors](./references/vibration-mode-vectors.md).
- Trajectory or animation: [trajectory](./references/trajectory-animation.md).
- Coordination or packing hulls: [polyhedra](./references/polyhedra.md).
- Panels or publication layouts: [panels](./references/multi-structure-panels.md)
  or [publication layout](./references/publication-layout.md).
- Finite periodic views or molecule focus: [periodic views](./references/periodic-finite-views.md)
  or [molecule focus](./references/molecule-highlight.md).

Use the default path when no specialized line matches. Load [input formats](./references/input-formats.md),
[camera](./references/camera.md), [backends](./references/cpu-static.md),
[capabilities](./references/capabilities-and-install.md), or
[verification](./references/verification.md) only when the requested evidence or an
actual failure requires it.

## Rules

- Use `ball_stick` for molecules and discrete covalent components. For dense
  extended coordination networks with polyhedra, start with `ball`.
- When a periodic figure must show coordination relative to the unit cell, use
  `--show-cell --show-axes`. Nonperiodic structures use neither.
- For render-only requests, do not invoke separate analysis skills or write Python;
  `mat-vis inspect` and the selected route are sufficient.
- Do not guess Python import names or inspect package internals. On failure, use
  the exact CLI help or `mat-vis capabilities --json`, correct the reported
  issue, and keep the requested scope.
- Do not install optional stacks unless the requested capability requires them.
- Preserve the literal command, JSON result, warnings, and output hash.
