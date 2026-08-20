---
name: visualize-materials
description: 'Visualize atomistic structures and trajectories with MatterVis. Use for CIF, Cube, POSCAR/CONTCAR, VASP, XYZ/extxyz, ASE .traj, LAMMPS dump/data, publication figures, static vector/raster export, animation, WebGL, and terminal views.'
---

# Visualize Materials

Create deterministic material visualizations with the public `mat-vis` CLI.
Treat chemical selection, camera, backend, requirements, and artifact checks as
explicit data.

## Route

1. Read [quickstart](./references/quickstart.md) for every task.
2. Run `mat-vis inspect INPUT --json`, then `mat-vis render ... --check --json`.
3. If preflight is unavailable, read [capabilities and installation](./references/capabilities-and-install.md)
   and use only the exact reported extra.
4. Read [diagnosis](./references/diagnose-and-select.md) before selecting the
   displayed object, and [camera](./references/camera.md) for images.
5. Read only the requested output path:
   - [CPU static PNG/PDF/SVG](./references/cpu-static.md) — default;
   - [Plotly HTML or explicit Plotly static export](./references/plotly-render.md);
   - [input formats](./references/input-formats.md);
   - [molecule focus](./references/molecule-highlight.md);
   - [polyhedra](./references/polyhedra.md);
   - [trajectory animation](./references/trajectory-animation.md);
   - [terminal TUI](./references/tui.md).
6. Read [verification](./references/verification.md) before delivery.

## Hard boundaries

- Base MatterVis is the complete CPU static path. Do not install Dash, Plotly,
  Kaleido, Textual, scikit-image, imageio, Chrome, or system libraries unless
  the requested capability requires them.
- Use public `mat-vis inspect`, `capabilities`, and `render --check` commands;
  do not diagnose through Web/TUI startup or private Python modules.
- Select `--backend cpu|plotly` explicitly. MatterVis has no silent backend or
  representation fallback; preserve a failure and its exact install hint.
- For a Web/API screenshot use requirement `web-screenshot`; for the Web UI's
  Plotly static export use `static-web-export`. Both resolve the required
  `[web,plotly-export]` combination rather than assuming one extra implies the
  other.
- Use orthographic lattice `+c` by default and record any other camera.
- Use MolCrysKit site, bond, ring, molecule, PBC, and formula-unit records. Do
  not reconstruct chemical identity from screen proximity.
- Keep comparable panels and animation frames on an explicit shared camera and
  physical-scale contract. Never alter geometry to hide whitespace.
- The agent CLI rejects legacy config/publication-layout flags it cannot honour.
  Report that boundary instead of invoking private compositors.
- Produce one requested artifact by default. Retain the JSON result, verify the
  file signature/hash/decoding, and keep visual acceptance pending until the
  final-size artifact has actually been reviewed.
