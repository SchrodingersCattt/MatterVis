# Matplotlib Flat ORTEP Path

Read this for browser-independent, camera-projected 2D ORTEP publication output.
Read `camera.md` first and `verification.md` before delivery.

## Exact dispatch

Matplotlib is selected only by both:

- `--material flat`
- `--style ortep`

`material=flat` alone remains Plotly 3D for ball, ball-stick, stick, and
wireframe styles.

## Capabilities and limits

- Uses Matplotlib's non-interactive `Agg` backend.
- Exports PNG, PDF, and SVG without Chrome.
- Uses camera-projected thermal ellipsoids and orthographic projection.
- Uses the active MatterVis element palette by default; set `monochrome=true`
  or pass `--monochrome` when a black-and-white figure is required.
- Is not equivalent to Plotly mesh, ball-and-stick, or interactive HTML.

Choose it directly for publication ORTEP or browser-independent static output.
If it is an automatic fallback, preserve the Plotly/Kaleido error and state that
the visual language changed.

## Command

```bash
mat-vis render INPUT.cif -o OUTPUT.pdf \
  --view formula_unit --style ortep --material flat \
  --camera-axis c --orthogonal \
  --width 1200 --height 900 --scale 2
```

Use `--show-hydrogen` explicitly when required. Report the effective backend as
`Matplotlib flat ORTEP`, not merely as PNG/PDF/SVG output.
