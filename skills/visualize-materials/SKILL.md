---
name: visualize-materials
description: "Use before drawing, showing, comparing, or animating atomistic structures, trajectories, polyhedra, or vibrations with MatterVis."
---

# Visualize Materials

Use the public `mat-vis` CLI for coordinate-based visuals. Do not replace it
with ASE plotting, raw matplotlib, or private MatterVis imports.

## Choose once

Read exactly one primary page and run its first documented path. Scene type wins: trajectory, vibration, and polyhedron routes take precedence over generic structure or molecule views:

- ordinary structure, molecule, slab, interface, or comparison:
  [static render](./references/quickstart.md);
- MD trajectory, final frame, GIF, or MP4:
  [trajectory](./references/trajectory-animation.md);
- vibration or phonon displacement arrows:
  [vibration](./references/vibration-mode-vectors.md);
- coordination or packing hulls:
  [polyhedra](./references/polyhedra.md);
- per-atom scalar/tensor colors:
  [atom properties](./references/atom-property-coloring.md);
- forces with density fields:
  [force vectors](./references/atomic-force-vectors.md);
- multiple panels or publication composition:
  [panels](./references/multi-structure-panels.md) or
  [layout](./references/publication-layout.md);
- explicitly requested periodic cropping or molecule highlighting:
  [periodic views](./references/periodic-finite-views.md) or
  [molecule focus](./references/molecule-highlight.md).

Read [runtime capabilities](./references/capabilities-and-install.md) only after
the direct command reports a missing capability. Read
[input formats](./references/input-formats.md), [camera](./references/camera.md),
[CPU details](./references/cpu-static.md), [Plotly](./references/plotly-render.md),
[TUI](./references/tui.md), or [verification](./references/verification.md) only
for an explicit request or unresolved ambiguity.

## Execution boundary

- Render directly; do not run a separate analysis or installation preflight.
- Use one backend and one representation. CPU is the default static path.
- Automatic view shows real periodic cells, hides synthetic/nonperiodic cells,
  and faces the largest lattice face.
- Run the documented command before inspecting help. Only after that exact command fails, inspect its help once and make one
  correction based on the exact error. Do not explore package internals, convert
  already-supported inputs, switch libraries, or enter an open-ended retry loop.
- Inspect the final-size artifact and retain the command, JSON result, warnings,
  and output hash.
