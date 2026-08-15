---
name: visualize-materials
description: 'Visualize atomistic structures and trajectories with MatterVis. Use for CIF, Cube, POSCAR/CONTCAR, VASP, XYZ/extxyz, ASE .traj, LAMMPS dump/data/configuration, molecular crystals, publication figures, static export, animation, and terminal views.'
---

# Visualize Materials

Create deterministic material visualizations. Treat object selection, disorder,
camera, style, export backend, and verification as explicit data.

## Core workflow

1. Verify the input and requested deliverable.
2. For non-CIF or multi-frame data, read [input formats](./references/input-formats.md).
3. Read [installation](./references/install.md) only when installation or repair is needed.
4. Always read [diagnosis and scene selection](./references/diagnose-and-select.md)
	 before choosing a display mode or style.
5. For images, always read the [camera contract](./references/camera.md), then the
	 matching render path.
6. By default export exactly one final PNG with explicit dimensions and settings.
   Generate HTML, PDF, SVG, animations, or extra candidate files only when the
   caller explicitly requests them.
7. Always read [verification and delivery](./references/verification.md) before
	 reporting or attaching the result.

## Render paths

- [Plotly 3D](./references/plotly-render.md): mesh, ball-and-stick, wireframe,
	non-ORTEP flat, interactive HTML, and normal static images.
- [Matplotlib flat ORTEP](./references/matplotlib-flat-ortep.md): intentional
	browser-independent 2D ORTEP output.

## Optional workflows

Read only when relevant:

- [Molecule focus and mixed styles](./references/molecule-highlight.md)
- [Atom- and molecule-centred polyhedra](./references/polyhedra.md)
- [Publication layout](./references/publication-layout.md)
- [Trajectory animation](./references/trajectory-animation.md)
- [Terminal TUI](./references/tui.md)

## Non-negotiable rules

- Use the installed `mat-vis` command for examples and automation. The Python
  package remains `crystal_viewer`; do not infer a second package name from the
  CLI rename.
- Diagnose before selecting `formula_unit`, `unit_cell`, `asymmetric_unit`, or
	`cluster`; a successful export does not prove a trustworthy scene.
- Default to an orthographic lattice `+c` view for crystals. Do not use an
	unrecorded interactive camera as the only delivered view.
- Use MatterVis/MolCrysKit canonical molecule identity, PBC unwrapping, and bonds.
	Never rebuild them from screen proximity or invent pseudo-elements for styling.
- Keep comparable panels and animation frames on explicit shared camera/scale
	contracts.
- Distinguish requested from effective display, style, material, backend, and
	fallback. A decoded PNG does not prove that the requested visual language ran.
- Do not emit HTML or a gallery of diagnostic variants for a normal static-image
  request. Keep failed attempts as diagnostics only; attach one selected PNG.
- A model without image-inspection capability reports objective checks and leaves
	visual acceptance pending; it never self-certifies publication quality.
