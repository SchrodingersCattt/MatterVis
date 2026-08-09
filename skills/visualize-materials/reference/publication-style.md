# Publication Display and Whitespace

Use this path for journal figures, N-up structural panels, compact subfigures,
or any request involving display preferences, too much whitespace, cropping,
compass placement, or inconsistent structure scale.

## Visual baseline

Prefer a restrained publication language:

- white figure and panel backgrounds;
- charcoal or dark-gray lines with thin widths;
- muted, stable semantic colors for elements, fragments, or evidence classes;
- direct labels only when they carry information;
- no rainbow palette, decorative gradient, fake shadow, or opaque patch hiding data;
- editable PDF/SVG when the downstream composition supports it, with a PNG preview.

Keep the chemistry and geometry unchanged while improving display. Do not add
atoms, bonds, surfaces, disorder branches, or labels that are not supported by
the source scene.

## Comparable structural panels

When panels are meant to be compared, share the following unless the figure
contract explicitly says otherwise:

- camera direction and screen-up;
- orthographic projection and display mode;
- world viewport or equivalent physical scale;
- element palette, atom/bond ratio, highlight convention, and background;
- output aspect ratio and intended physical size.

Use `uniform_viewport(scenes, padding=...)` for N-up scenes that must share
length-per-pixel. For a publication row using `material="flat"`, keep the
fixed `flat_visual_pixel_scale` (default `30.0`) and change framing or canvas
size rather than changing atom and bond sizes independently. Record the chosen
scale in the figure metadata or manifest.

Do not mix a depth-shaded `mesh` panel with a billboard `flat` panel in a
comparison row unless the difference is part of the scientific message and is
called out in the caption or manifest.

## Separate the four kinds of empty space

Diagnose whitespace before changing geometry. Treat these as different things:

1. **Scene viewport**: the world-coordinate range used to frame atoms, bonds,
   cell edges, and overlays.
2. **Renderer/export margin**: whitespace introduced by Plotly, Kaleido, or a
   Matplotlib canvas around the rendered scene.
3. **Composition gutter**: intentional space between panels, row labels, legends,
   and captions.
4. **True data whitespace**: empty space required by the crystal geometry,
   vacuum layer, slab termination, or a meaningful annotation.

Fix the smallest layer that caused the defect. If the structure occupies only a
small fraction of the PNG, inspect the renderer viewport and export margin
before changing the camera, cell, atom radius, or coordinate system.

## Cropping contract

For a compact structure panel:

- crop only contiguous background pixels at the paper border;
- keep a small explicit safety pad (for example, `18 px` for a high-resolution
  `2400 px` render, exposed as a parameter rather than hard-coded in review);
- preserve the center and aspect ratio;
- never resize x and y independently;
- do not resample or isotropically rescale the structure as a hidden side effect
  of cropping; record `rescaled=false` when a manifest is used;
- keep enough clearance for atoms, bonds, slab edges, and the compass.

Cropping changes the paper rectangle, not the scientific scene. A crop must not
alter camera direction, lattice coordinates, atom/bond ratio, flat marker size,
or the relative position of an axis key. If those need to change, make them
explicit render parameters and rerun QA.

## Compass, labels, and safety bands

Render decorations in a separate layer after the structure layer, but place them
in real whitespace rather than over a bond or atom. Axis keys and compass arrows
must have equal display-pixel lengths when they encode direction only. Reserve
explicit row and column safety bands for multi-panel figures; adjacent panel
letters, tick labels, legends, and spines must not invade those bands.

Use a short figure contract before rendering:

```text
Output stem and formats:
Target physical size:
Panel roles and shared camera/scale rules:
Display mode, style, material, and palette:
Required highlights, labels, compass, and legends:
Forbidden crops, dropped content, or non-proportional scaling:
Programmatic checks and final-size inspection:
```

At final size, aim for approximately `88 mm` for a single-column figure or
`180 mm` for a double-column figure, at least `7 pt` for ordinary text and
`8 pt` for captions, axes, ticks, and legends. Export raster panels at roughly
`200–600 dpi` at their final physical size. These are defaults, not permission
to shrink a crowded panel until it becomes unreadable.

## Verification

For each exported panel, record:

- original and cropped dimensions;
- crop box and border padding;
- whether rescaling occurred;
- camera, projection, display mode, and material;
- flat visual scale or shared world viewport;
- compass anchor and minimum clearance from the structure.

Decode the PNG and check the PDF/SVG signature. Inspect the final-size image,
not only the plotting script or an interactive preview. For a panel grid, check
that comparable panels retain the same scale and that the declared gutters are
background-dominant.
