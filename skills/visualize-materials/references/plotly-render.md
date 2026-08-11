# Plotly 3D Render Path

Read this for ball-and-stick, mesh ORTEP, wireframe, interactive HTML, or any
request that must preserve real 3D geometry. Read `diagnose-and-select.md` and
`camera.md` first, then `verification.md` before delivery.

## Semantics

- `mat-vis render` accepts one CIF input and writes `.png`, `.pdf`, `.svg`, or
  `.html` according to the output suffix.
- `material=mesh` uses Plotly `Mesh3d` atoms and bonds.
- `material=flat` with a non-ORTEP style remains Plotly 3D and uses fixed-screen
  `Scatter3d` primitives.
- HTML is interactive and does not require local Chrome to write.
- PNG, PDF, and SVG use Kaleido and may require Chrome/Chromium.

For an admitted small, chemically resolved, low-disorder formula unit, start
with `formula_unit`, `ball_stick`, and `mesh`. Do not apply that recipe when the
diagnosis rejects it.

## Commands

```bash
mat-vis render INPUT.cif -o OUTPUT.png \
  --view formula_unit --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --no-hydrogen --show-cell --show-axes --no-labels \
  --background '#FFFFFF' --atom-scale 1.0 --bond-radius 0.15 \
  --width 1200 --height 900 --scale 2
```

For interactive output:

```bash
mat-vis render INPUT.cif -o OUTPUT.html \
  --camera-axis c --orthogonal
```

Use `--show-hydrogen` explicitly when hydrogens matter; the image CLI hides them
by default. Use `--config` for supported style fields such as disorder treatment.
Live 0.0.0 render choices are:

- views: `formula_unit`, `unit_cell`, `asymmetric_unit`, `cluster`;
- styles: `ball_stick`, `ball`, `stick`, `ortep`, `wireframe`;
- materials: `mesh`, `flat`;
- visibility: hydrogen, cell, axes, labels, and monochrome;
- appearance: atom scale, bond radius, background, dimensions, scale;
- ORTEP probability/mode and JSON view-scoring weights.

## Large scenes

Do not blindly repeat expensive combinations after a warning reports thousands
of expanded atoms or a large distance matrix. Diagnose first, then change the
selection/disorder policy. Lowering scale or switching mesh to flat changes
export cost but does not repair an invalid chemical selection.

## Failure behavior

Let the static command perform the decisive Kaleido check. If unavailable,
MatterVis may fall back to Matplotlib flat ORTEP. Preserve the original error and
never describe the fallback as equivalent to requested mesh, flat-stick,
ball-and-stick, or wireframe output.
