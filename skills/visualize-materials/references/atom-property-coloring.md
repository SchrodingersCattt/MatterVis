# Per-Atom Property Coloring

Read this for charge, velocity, stress, per-atom scalar/vector/tensor fields,
colormaps, or continuous colorbars. For an animation, also apply the fixed
camera and streaming rules in `trajectory-animation.md`.

## Inspect before choosing semantics

Always start with bounded discovery:

~~~bash
mat-vis inspect INPUT --properties --json
~~~

Add `--property-data MANIFEST` when the field is an external sidecar. Confirm
the qualified field source, dtype, trailing shape, declared components, unit,
source atom key, and source frame/timestep key. Bare names are valid only when
they resolve uniquely; otherwise use `array:NAME`, `column:NAME`, or
`sidecar:NAME`.

## Reduction and range

- Scalar fields use `--color-reduction scalar` or `auto`.
- Three-component velocity defaults to magnitude under `auto`.
- Tensor fields must explicitly use `component`, `trace`, `mean_normal`, or
  `von_mises`. Never let `auto` assign tensor meaning.
- A six-component tensor requires declared component order in the sidecar.
- Charge may use a diverging colormap with `--color-center 0` only when zero is
  the physically meaningful neutral reference. Do not infer this from the
  field name.
- Animation range is the exact finite min/max over every selected source atom
  in every selected source frame. Never rescale each frame independently.
- Use `--color-range MIN MAX` only when a scientifically chosen fixed scale is
  required; values outside it clip and the explicit range skips the prescan.
- Units are labels/provenance only. MatterVis performs no implicit conversion.

Example:

~~~bash
mat-vis render charged.extxyz -o charge.png --backend cpu \
  --color-by array:charges --colormap coolwarm --color-center 0 \
  --color-label Charge --color-unit e --json

mat-vis render run.dump --type-map Si O -o speed.mp4 --backend cpu \
  --frame-range 0:1000:10 --property-data fields.json \
  --color-by sidecar:velocity --color-reduction magnitude --json
~~~

## Sidecars and large systems

Use the `mattervis.atom-properties/v1` JSON + read-only NPY sidecar format.
Prefer an embedded LAMMPS/extxyz column when available; otherwise use mmap NPY.
Do not convert large fields to CSV or NPZ, do not materialize `F×N` values, and
do not sample atoms. The large-LAMMPS path streams only coordinates, identity,
and selected property columns. Repeat occurs after strict ID/label/row
alignment and then tiles the resolved values.

For `id` or `label`, verify uniqueness and an exact atom set on every selected
frame. For `row`, require and verify the source SHA-256. A v1 trajectory sidecar
has fixed atom count and frame IDs; extra unselected sidecar frames are allowed.

## Verification

Inspect the render JSON metadata and verify `fields`, effective `reduction`,
`range`, `range_scope`, `unit`, `finite_count`, `missing_count`, `lut_hash`, and
`manifest_hash`. Non-finite values must be counted and use `nan_color`; an
all-non-finite selection is an error. Confirm explicit atom-group colors win.
By default each bond half must inherit its endpoint's final atom color, giving
two-tone bonds for different property values. An explicit bond-group color must
override both halves. Check first/middle/last frames and the frames containing
property extrema.
