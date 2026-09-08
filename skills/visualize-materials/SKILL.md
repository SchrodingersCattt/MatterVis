---
name: visualize-materials
description: "Use first for every atomistic drawing, model preview, or animation with MatterVis, including structures, trajectories, polyhedra, and vibrations."
---

# Visualize Materials

Use the public `mat-vis` CLI for coordinate-based visuals. Do not replace it
with ASE plotting, raw matplotlib, or private MatterVis imports.

## Choose the scene route

Read one scene-specific primary page and run its first path. Scene type wins:

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
- multiple panels or publication composition: [panels](./references/multi-structure-panels.md)
  or [layout](./references/publication-layout.md);
- cube orbitals, electron density, or scalar-field panel grids:
  [cube isosurfaces](./references/cube-isosurface-panels.md);
- explicitly requested periodic cropping or molecule highlighting:
  [periodic views](./references/periodic-finite-views.md) or
  [molecule focus](./references/molecule-highlight.md).

Before delivery, always read [verification](./references/verification.md). For
two or more panels also read [panels](./references/multi-structure-panels.md);
for print/publication output also read [layout](./references/publication-layout.md).

Read [runtime capabilities](./references/capabilities-and-install.md) only after
the command reports a missing capability. Read
[input formats](./references/input-formats.md), [camera](./references/camera.md),
[CPU details](./references/cpu-static.md), [Plotly](./references/plotly-render.md),
[TUI](./references/tui.md) only for an explicit request or unresolved ambiguity.

## Execution boundary

- Molecules and covalent networks default to `ball_stick`; use `ball` for dense
  ionic or coordination structures only when bonds are not evidence.
- If rendering starts later or is delegated, that agent loads this skill before
  choosing a command; parent summaries and guessed flags are not a substitute.
- Render directly; do not run a separate analysis or installation preflight.
- Use one backend and one representation. CPU is the default static path.
- Memory pressure may change renderer detail, never the model: do not delete H,
  downsample atoms, shrink the structure, or switch away from `mat-vis`.
- Comparison renders use the same orientation, canvas, and orthographic scale.
- Automatic view shows real periodic cells, hides synthetic/nonperiodic cells,
  and faces the largest lattice face.
- Run the documented command before inspecting help. Only after it fails,
  inspect help once and correct the exact error; avoid an open-ended retry loop.
- Inspect final-size output; retain the command, JSON result, warnings, and hash.
