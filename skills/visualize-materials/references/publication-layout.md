# Publication Layout

Read this for journal figures, N-up panels, compact subfigures, whitespace,
cropping, compass placement, or comparable structural scale.

For any image containing more than one molecular/crystal structure, also read
[multi-structure panels](./multi-structure-panels.md). It defines the required
scale declaration, the five distinct bounding layers, and objective occupancy
checks; this file does not replace that workflow.

## Visual baseline

Prefer white backgrounds, thin charcoal marks, restrained semantic colours, and
direct labels only when informative. Avoid decorative gradients, rainbow
palettes, fake shadows, unnecessary titles, and opaque overlays. Keep chemistry
and geometry unchanged. Prefer editable PDF/SVG with a PNG preview when the
downstream composition supports it.

## Dense coordination polyhedra

For a packed unit cell plus isolated coordination panels, use
`--publication-layout`, `--view unit_cell`, explicit polyhedron specifications,
`--publication-preset dense_coordination`, and `--publication-style blender`.
The preset controls composition; `blender` supplies the verified material,
lighting, line, and sphere defaults. Pass deviations with repeated
`--publication-option PATH=VALUE` arguments, which are applied last; do not
require a project-specific style file.

Generate the final static file through `mat-vis render`. Direct Python entry
points and builder calls are for tests and debugging; do not deliver their
output as the CLI artifact.

Neither the layout preset nor the visual style contains a camera; select the
view separately for the structure. The `blender` name identifies this
MatterVis profile and does not imply a Blender rendering backend.

The verified material signature is flat translucent green/purple/blue
polyhedra (CN 8/6/4), faint hull edges, and glossy coral ligand spheres. Keep
native Matplotlib face shading off and use the controlled camera-aware
polyhedron material instead (ambient 0.45, diffuse 0.55, alpha preserved).
Sphere lighting uses ambient 0.72 and diffuse 0.28; ligands use `#FF6363`.
For a packed main cell, use family-specific roles rather than uniform
transparency: CN8 alpha/light-strength 0.34/0.18, CN6 0.72/0.55, and CN4
0.78/0.65. Draw edges only on front-facing hull triangles in the same
depth-sorted face collection, and omit centre-to-ligand spokes. Isolated panels retain face alphas 0.52/0.53/0.50. Draw rear-only hull edges
and every interior spoke below the translucent face stack, then front-facing
hull edges above it; never place a complete wireframe above the polyhedron.

Select main polyhedra by centre in the half-open fractional cell
`0 <= f < 1`. Render mixed occupancies as weighted sphere sectors. In isolated
panels, draw rear ligands before the translucent polyhedron and front ligands
after it. Verify canonical counts, complete front/back partitioning, and exact
saved dimensions. The complete parameter table is in
`docs/agents/static_publication.md` in the MatterVis repository.


## Comparable panels

Share camera direction and screen-up, orthographic projection, display mode,
world viewport or physical scale, palette, atom/bond ratio, highlight convention,
background, aspect ratio, and intended physical size.

Use `uniform_viewport(scenes, padding=...)` when N-up scenes need shared
length-per-pixel. For `material="flat"`, retain the fixed
`flat_visual_pixel_scale` (default `30.0`) and change framing or canvas size
rather than atom and bond sizes independently. Do not mix mesh depth shading and
flat billboards unless that contrast is scientifically intentional and disclosed.

## Separate empty-space sources

Diagnose before changing geometry:

1. scene viewport;
2. renderer/export margin;
3. composition gutter;
4. true data whitespace from crystal geometry, vacuum, or annotation.

Fix the smallest layer that caused the defect. If the structure occupies little
of the PNG, inspect viewport and export margin before changing camera, cell,
radii, or coordinates.

## Cropping contract

Crop only contiguous background at the paper border. Keep an explicit safety
pad, preserve centre and aspect ratio, and never resize x and y independently.
Cropping must not resample the structure, alter its scale, hide a camera/geometry
problem, or change the relative compass position. Record original/cropped
dimensions, crop box, padding, and whether rescaling occurred.

When independent panel crops have different widths, preserve them at native
pixel size and use variable column widths. Do not stretch them into equal-width
cells. When shared Å/pixel is required, padding a narrow crop is allowed;
rescaling it is not.

## Compass and safety bands

Render decorations separately and place them in real whitespace. Axis-key arrows
must have equal display-pixel lengths when they encode direction only. Reserve
row and column safety bands so panel letters, labels, legends, and spines do not
invade adjacent panels.

Use a short figure contract:

```text
Output stem and formats:
Target physical size:
Panel roles and shared camera/scale rules:
Display mode, style, material, and palette:
Required highlights, labels, compass, and legends:
Forbidden crops, dropped content, or non-proportional scaling:
Programmatic checks and final-size inspection:
```

As defaults, target about 88 mm for a single-column or 180 mm for a double-column
figure, at least 7 pt ordinary text and 8 pt captions/axes/ticks/legends, and
roughly 200–600 dpi at final physical size. Do not shrink a crowded panel until
it becomes unreadable.
