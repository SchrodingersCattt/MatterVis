---
name: visualize-materials
description: 'Visualize crystal and molecular material structures with MatterVis. Use when rendering CIF files, diagnosing display modes or disorder, making publication figures, choosing crystallographic cameras, exporting PNG/PDF/SVG/HTML, highlighting complete molecules, or producing trajectory animations and terminal views.'
---

# Visualize Materials

Create deterministic material visualizations. Treat object selection, disorder,
camera, style, export backend, and verification as explicit data.

## Core workflow

1. Verify the input and requested deliverable.
2. Read [installation](./references/install.md) and verify the released CLI.
3. Always read [diagnosis and scene selection](./references/diagnose-and-select.md)
	 before choosing a display mode or style.
4. For images, always read the [camera contract](./references/camera.md), then the
	 matching render path.
5. Export with explicit dimensions and settings.
6. Always read [verification and delivery](./references/verification.md) before
	 reporting or attaching the result.

## Render paths

- [Plotly 3D](./references/plotly-render.md): mesh, ball-and-stick, wireframe,
	non-ORTEP flat, interactive HTML, and normal static images.
- [Matplotlib flat ORTEP](./references/matplotlib-flat-ortep.md): intentional
	browser-independent 2D ORTEP output.

## Optional workflows

Read only when relevant:

- [Molecule focus and mixed styles](./references/molecule-highlight.md)
- [Publication layout](./references/publication-layout.md)
- [Trajectory animation](./references/trajectory-animation.md)
- [Terminal TUI](./references/tui.md)

## Non-negotiable rules

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
- A model without image-inspection capability reports objective checks and leaves
	visual acceptance pending; it never self-certifies publication quality.
