---
name: visualize-materials
description: "Use for any atomistic image or visualization: structures, molecules, crystals, slabs, trajectories, polyhedra, vibrations, fields, animations, comparisons, and publication figures."
---

# Visualize Materials

Create deterministic material visualizations with the public `mat-vis` CLI.
Treat chemical selection, camera, backend, requirements, and artifact checks as
explicit data.

## Route

1. Read [quickstart](./references/quickstart.md) for every task.
2. Run `mat-vis inspect INPUT --json`, then render directly with the base CPU path.
3. Only when the requested path is unavailable, read [capabilities and installation](./references/capabilities-and-install.md)
   and use the exact reported extra; do not install optional stacks speculatively.
4. Read [diagnosis](./references/diagnose-and-select.md) before selecting the
   displayed object, and [camera](./references/camera.md) for images.
5. Read only the requested output path:
   - [CPU static PNG/PDF/SVG](./references/cpu-static.md) — default;
   - [Matplotlib projected 2D PNG/PDF/SVG](./references/cpu-static.md);
   - [Plotly HTML or explicit Plotly static export](./references/plotly-render.md);
   - [input formats](./references/input-formats.md);
   - [molecule focus](./references/molecule-highlight.md);
   - [periodic finite views](./references/periodic-finite-views.md);
   - [polyhedra](./references/polyhedra.md);
   - [atom-centred forces with density fields](./references/atomic-force-vectors.md);
   - [vibration displacement arrows and optional GIFs](./references/vibration-mode-vectors.md);
   - [trajectory animation](./references/trajectory-animation.md);
   - [publication layout](./references/publication-layout.md);
   - [multi-structure panels](./references/multi-structure-panels.md);
   - [terminal TUI](./references/tui.md).
6. Read [verification](./references/verification.md) before delivery.

## Hard boundaries

- Base MatterVis is the complete CPU static path. Do not install Dash, Plotly,
  Kaleido, Textual, scikit-image, imageio, Chrome, or system libraries unless
  the requested capability requires them.
- Use public `mat-vis inspect`, `capabilities`, and `render` commands;
  do not diagnose through Web/TUI startup or private Python modules.
- Pass vibration and other anchored vectors through public `mat-vis render
  --vector-overlays`; never invoke private renderer internals.
- A failed or poor-looking MatterVis artifact is not permission to redraw
  atomistic geometry with ASE or raw plotting code. Adjust supported `mat-vis`
  controls, or retain the evidence and report the unresolved limitation.
- Select `--backend cpu|matplotlib|plotly` explicitly. Use `matplotlib` for a
  projected 2D drawing and `cpu` for 3D geometry. MatterVis has no silent backend or
  representation fallback; preserve a failure and its exact install hint.
- For a Web/API screenshot use requirement `web-screenshot`; for the Web UI's
  Plotly static export use `static-web-export`. Both resolve the required
  `[web,plotly-export]` combination rather than assuming one extra implies the
  other.
- For uniformly compressed or expanded structures, prefer one positive global
  MolCrysKit `bond_scale` before introducing element-pair thresholds. Use the
  same scale for source molecule perception and displayed bonds, then verify
  intended bonds and false contacts on the actual structure.
- Periodic inputs default to orthographic lattice `+c`. Nonperiodic inputs use
  atom-content fitting, `cluster`, and no cell; a synthetic ASE padding cell has
  no crystallographic meaning. Explicit user choices override these defaults and
  must be recorded.
- Use MolCrysKit site, bond, ring, molecule, PBC, and formula-unit records. Do
  not reconstruct chemical identity from screen proximity.
- A finite image of a periodic chain, layer, or framework must follow
  [periodic finite views](./references/periodic-finite-views.md). Do not infer
  periodic dimensionality from the camera or replicate whole cells merely to
  make a boundary look balanced.
- Keep comparable panels and animation frames on an explicit shared camera and
  physical-scale contract. Never alter geometry to hide whitespace.
- The agent CLI rejects legacy config/publication-layout flags it cannot honour.
  Report that boundary instead of invoking private compositors.
- Produce one requested artifact by default. Retain the JSON result, verify the
  file signature/hash/decoding, and keep visual acceptance pending until the
  final-size artifact has actually been reviewed.
