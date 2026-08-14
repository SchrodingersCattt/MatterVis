---
name: visualize-materials
description: 'Visualize crystal and molecular material structures with MatterVis. Use when rendering CIF files, diagnosing display modes or disorder, making publication figures, choosing crystallographic cameras, exporting PNG/PDF/SVG/HTML, highlighting complete molecules, or producing trajectory animations and terminal views.'
---

# Visualize Materials

Create deterministic material visualizations with the installed `mat-vis` CLI.
Treat scene selection, camera, backend, and artifact verification as evidence.

## Route

1. Read [quickstart](./references/quickstart.md) for every task.
2. Read [installation](./references/install.md) only when installing or repairing
   Chrome/Kaleido.
3. Read [diagnosis](./references/diagnose-and-select.md) for disorder, ambiguous
   chemistry, or scenes above 200 visible atoms.
4. Read [camera](./references/camera.md) only for non-default views or comparable
   panels.
5. Read one specialized path when needed:
   - [Matplotlib flat ORTEP](./references/matplotlib-flat-ortep.md)
   - [Molecule focus](./references/molecule-highlight.md)
   - [Polyhedra](./references/polyhedra.md)
   - [Publication layout](./references/publication-layout.md)
   - [Trajectory animation](./references/trajectory-animation.md)
   - [Terminal TUI](./references/tui.md)
6. Read [verification](./references/verification.md) only for non-PNG outputs or
   troubleshooting the verified-render wrapper.

## Hard boundaries

- Use `mat-vis`, never `python -m crystal_viewer`, for delivered renders.
- Install into the caller's current Python environment by default. Use an
  optional venv only when requested or needed for a known dependency conflict.
- Diagnose before choosing the displayed object. Export success is not chemistry
  or semantic validation.
- Use orthographic lattice `+c` by default. Record any other camera explicitly.
- Use MatterVis/MolCrysKit molecule identity, PBC unwrapping, and bonds; do not
  reconstruct them from screen proximity.
- Produce one requested final artifact by default, not a gallery.
- Run delivery renders through `scripts/render_verified.py`. A file that is
  undecodable, blank, a mismatched fallback, or unverified is not deliverable.
- Keep requested and effective display/style/material/backend distinct.
- Models without image inspection report objective checks and leave visual
  acceptance pending.
