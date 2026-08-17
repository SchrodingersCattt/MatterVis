---
name: visualize-materials
description: 'Visualize atomistic structures and trajectories with MatterVis. Use for CIF, Cube, POSCAR/CONTCAR, VASP, XYZ/extxyz, ASE .traj, LAMMPS dump/data/configuration, molecular crystals, publication figures, static export, animation, and terminal views.'
---

# Visualize Materials

Create deterministic material visualizations with the installed `mat-vis` CLI.
Treat scene selection, camera, backend, and artifact verification as evidence.

## Route

1. Read [quickstart](./references/quickstart.md) for every task.
2. For non-CIF or multi-frame data, read [input formats](./references/input-formats.md).
3. Read [installation](./references/install.md) only when installing or repairing
   Chrome/Kaleido.
4. Always read [diagnosis](./references/diagnose-and-select.md) before choosing a
   display mode or style.
5. For images, always read the [camera contract](./references/camera.md), then
   one specialized path when needed:
   - [Plotly 3D](./references/plotly-render.md)
   - [Matplotlib flat ORTEP](./references/matplotlib-flat-ortep.md)
   - [Molecule focus](./references/molecule-highlight.md)
   - [Polyhedra](./references/polyhedra.md)
   - [Publication layout](./references/publication-layout.md)
  - [Multi-structure panels](./references/multi-structure-panels.md)
   - [Trajectory animation](./references/trajectory-animation.md)
   - [Terminal TUI](./references/tui.md)
6. Always read [verification](./references/verification.md) before reporting or
   attaching the result.

## Hard boundaries

- Use `mat-vis`, never `python -m crystal_viewer`, for delivered renders.
- Install into the caller's current Python environment by default. Use an
  optional venv only when requested or needed for a known dependency conflict.
- Diagnose before choosing the displayed object. Export success is not chemistry
  or semantic validation.
- Use orthographic lattice `+c` by default. Record any other camera explicitly.
- Use MatterVis/MolCrysKit molecule identity, PBC unwrapping, and bonds; do not
  reconstruct them from screen proximity or invent pseudo-elements for styling.
- Keep comparable panels and animation frames on explicit shared camera/scale
  contracts.
- For every multi-structure figure, declare whether panel scale is shared
  (physical size is comparable) or independently fitted (shape/direction only).
  Never use camera distance, viewport padding, or nonuniform scaling as an
  undocumented cure for whitespace.
- Produce one requested final artifact by default, not a gallery.
- Run the literal `mat-vis` command. Retain its exit code and logs, decode
  the output, and inspect the final artifact before delivery.
- Keep requested and effective display/style/material/backend distinct.
- Models without image inspection report objective checks and leave visual
  acceptance pending.
