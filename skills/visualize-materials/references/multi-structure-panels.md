# Multi-Structure Panels

Read this whenever one delivered image contains two or more molecular, crystal,
polyhedral, orbital, or trajectory panels. The central rule is:

> A panel has a world-space structure, a camera projection, a renderer-owned
> scene domain, a composition cell, and a paper canvas. Diagnose those layers in
> that order; do not change an upstream layer to hide whitespace created later.

This workflow must be executable from geometry and image measurements. It must
not assume that the model can visually judge whether a panel "looks filled".

## 1. Declare what is comparable

Before rendering, choose and record exactly one scale contract:

### Shared physical scale

Use when molecular/crystal dimensions, bond lengths, lattice spans, or apparent
object sizes are part of the comparison.

- share camera direction and screen-up;
- use orthographic projection unless perspective is scientifically required;
- use a shared world viewport or `uniform_viewport`;
- keep the same Å per rendered pixel;
- accept true whitespace around smaller structures;
- crop only common outer paper borders unless variable panel widths are allowed;
- never enlarge a smaller structure independently.

### Independent fit

Use when panels compare topology, orientation, colour, direction, or local detail
and physical size is not encoded by apparent size.

- each structure may receive its own isotropic viewport fit;
- preserve x/y/z aspect ratio and all molecular geometry;
- state that apparent panel sizes are not physically comparable;
- do not silently switch from shared scale to independent fit to reduce whitespace.

If the user has not stated the contract and it affects interpretation, ask. A
reasonable default for a publication comparison is shared physical scale; a
reasonable default for unrelated molecule direction icons is independent fit.

## 2. Keep five boundaries separate

For every panel, distinguish:

1. **Structure bounds** — Cartesian extent of rendered atoms, bonds, surfaces,
   cell corners, and any world-space arrows. Atom and bond radii matter; atom
   centres alone are insufficient.
2. **Projected bounds** — the structure bounds after the selected camera and
   projection, before paper annotations.
3. **Scene domain** — the rectangular Plotly/Matplotlib region reserved for the
   3D axes. A square/cube-preserving scene inside a wide column can create large
   horizontal gutters even when the world viewport is tight.
4. **Composition cell** — the row/column slot assigned by the subplot layout,
   including inter-panel gutters and equal-width constraints.
5. **Paper bounds** — title, panel letter, legend, colour bar, arrow, caption,
   and export margins.

Changing structure coordinates, atom radii, camera distance, or axis aspect to
fix a composition-cell gutter is a category error.

## 3. Diagnose whitespace numerically

Do not start by zooming. Record:

- output pixel dimensions;
- panel domains in paper coordinates;
- x/y/z world ranges and projection type;
- projected structure bounding box per panel;
- final non-background ink bounding box per panel;
- left/right/top/bottom safety pads;
- structure occupancy = ink area or ink bounding-box area divided by panel area;
- whether ink touches a panel edge;
- whether title, labels, arrows, legends, or colour bars own the observed space.

Use `scripts/check_panel_layout.py` for objective PNG measurements. Its defaults
are acceptance guidance, not universal law, and every threshold has a CLI
override.

Default formal-figure targets:

- ink bounding-box occupancy: 70–95% of each declared panel rectangle;
- minimum safety pad: 24 px at the final exported resolution;
- no ink touching a panel edge;
- no all-background panel;
- no unexplained occupancy ratio above 1.35 between independently fitted panels;
- titles and bottom annotations must be inside the measured panel allocation or
  inside an explicitly reserved shared band.

For sparse line drawings, occupancy below 70% can be legitimate. Override the
threshold and record why; do not mutate geometry merely to satisfy the number.

## 4. Fix the smallest responsible layer

Apply fixes in this order:

1. **Export margin**: reduce only unused paper border.
2. **Composition gutter**: reduce subplot spacing or use variable column widths.
3. **Scene domain**: let the 3D domain occupy more of its composition cell.
4. **World viewport**: tighten isotropically around radius-aware rendered bounds.
5. **Camera**: change direction only to clarify geometry; change orthographic
   zoom only when it is part of the declared scale contract.
6. **Geometry/style**: change atom scale, bond radius, hydrogen visibility, or
   coordinates only for a chemical/readability reason, never merely for layout.

Forbidden whitespace fixes:

- independent x/y stretching;
- changing lattice vectors or molecular coordinates;
- hiding atoms/bonds without a semantic reason;
- increasing atom/bond size to fake occupancy;
- moving a camera differently in panels that claim shared orientation;
- cropping before decorations are placed;
- claiming equal physical scale after independent fitting or resampling.

## 5. Cropping and variable-width composition

Per-panel cropping is allowed and recommended for an independently fitted icon
or direction panel when a renderer reserves excess internal scene space.

Required contract:

- crop only contiguous background outside the final ink;
- include titles, panel letters, arrows, labels, and legends in the ink mask;
- add an explicit safety pad;
- do not resample the crop;
- preserve each panel's x/y aspect ratio;
- join crops at native pixel size;
- allow variable column widths rather than stretching crops into equal columns;
- record raw dimensions, crop boxes, padding, gutter, final dimensions, and
  `rescaled=false`.

For shared physical scale, per-panel native-pixel cropping and variable column
widths preserve Å/pixel; resizing each crop to equal width destroys the shared
scale. If equal columns are mandatory, pad the narrower crop instead of scaling
it.

## 6. Decorations and arrows

World-space overlays belong in structure/projected bounds. Paper-coordinate
arrows, panel letters, titles, and values belong in paper bounds.

- place decorations before measuring final ink;
- use paper-coordinate arrows to avoid atom occlusion when direction only is
  encoded;
- use equal pixel arrow lengths only when magnitude is printed separately;
- if arrow length encodes magnitude, use one shared scale across panels;
- keep text and arrowheads inside the safety pad;
- interaction-only Plotly picking traces must be omitted from static exports
  with `include_interaction_traces=False`.

## 7. Deterministic validation without visual ability

A model that cannot inspect images must not say "looks good". It must instead:

1. decode the final artifact;
2. run the panel-layout checker with explicit panel boundaries;
3. report occupancy and all four pads for every panel;
4. compare requested versus effective camera, projection, scale contract, and
   crop/resample metadata;
5. report visual acceptance as pending human review.

A model with image inspection must still run the objective checks; vision does
not replace boundary arithmetic.

## 8. Required delivery record

For each multi-panel artifact record:

```text
Panel purpose and order:
Scale contract: shared physical scale | independent fit
Camera/projection contract:
Raw and final dimensions:
Panel domains or pixel boundaries:
Per-panel ink bbox, occupancy, and safety pads:
Crop boxes, gutter, and resampling status:
Decorations included in bounds:
Automated pass/fail and overridden thresholds:
Human visual acceptance: pass | pending | fail
```

Do not deliver a multi-panel figure until those fields are known or explicitly
marked pending.
