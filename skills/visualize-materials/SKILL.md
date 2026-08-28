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

Choose one specialized reference first; do not also load quickstart:

- Coordination or packing hulls: [polyhedra](./references/polyhedra.md).
- Vibration arrows: [vibration vectors](./references/vibration-mode-vectors.md).
- Trajectory or animation: [trajectory](./references/trajectory-animation.md).
- Finite periodic views or molecule focus: [periodic views](./references/periodic-finite-views.md)
  or [molecule focus](./references/molecule-highlight.md).
- Panels or publication layouts: [panels](./references/multi-structure-panels.md)
  or [publication layout](./references/publication-layout.md).

For an ordinary static view use [quickstart](./references/quickstart.md). Only
when needed, load [diagnosis](./references/diagnose-and-select.md),
[input formats](./references/input-formats.md), [camera](./references/camera.md),
[backends](./references/cpu-static.md),
[capabilities](./references/capabilities-and-install.md), or
[verification](./references/verification.md).

## Rules

- Use `ball_stick` for molecules and discrete covalent components. For dense
  extended coordination networks with polyhedra, start with `ball`.
- When a periodic figure must show coordination relative to the unit cell, use
  `--show-cell --show-axes`. Nonperiodic structures use neither.
- Keep MatterVis colors unless color carries evidence. Do not name a display color
  unless requested; then quote render JSON `effective_colors` exactly.
- Do not guess Python import names or inspect package internals. On failure, use
  the exact CLI help or `mat-vis capabilities --json`, correct the reported
  issue, and keep the requested scope.
- Do not install optional stacks unless the requested capability requires them.
- Preserve the literal command, JSON result, warnings, and output hash.
