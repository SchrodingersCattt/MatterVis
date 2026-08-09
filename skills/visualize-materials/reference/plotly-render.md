# Plotly 3D Render Path

Use this path for ball-and-stick, mesh ORTEP, wireframe, interactive HTML, or any request that must preserve real 3D geometry.

## Semantics

- `material=mesh` uses Plotly `Mesh3d` atoms and bonds.
- `material=flat` with a non-ORTEP style is still Plotly 3D; it uses fixed-screen-size `Scatter3d` primitives.
- HTML output is interactive and does not require local Chrome to write.
- PNG, PDF, and SVG use Kaleido and may require Chrome/Chromium.

## Accessible defaults

Unless overridden, set:

- `--camera-axis c`
- `--orthogonal`
- `--camera-up` omitted, which selects `+b` for a lattice and `+Y` otherwise
- `--view formula_unit`
- `--style ball_stick`
- `--material mesh`
- explicit `--width`, `--height`, and `--scale`

Do not use an unrecorded interactive camera as the final view.

## Camera controls

Choose exactly one direction control:

- `--camera-axis a|b|c|a*|b*|c*`
- `--view-direction X Y Z`
- `--camera-position X Y Z`

Optional controls:

- `--camera-up X Y Z`
- `--camera-distance D`
- `--orthogonal` or explicitly requested `--perspective`

`--camera-position` is relative to the scene centre. `--view-direction` points from the scene toward the camera. All vectors must be finite and non-zero.

## Command

    python -m crystal_viewer render INPUT.cif -o OUTPUT.png \
      --view formula_unit --style ball_stick --material mesh \
      --camera-axis c --orthogonal \
      --width 1200 --height 900 --scale 2

For interactive output:

    python -m crystal_viewer render INPUT.cif -o OUTPUT.html \
      --camera-axis c --orthogonal

## Failure behavior

For static output, let the command perform the decisive Kaleido export check. If Chrome/Kaleido is unavailable, MatterVis falls back to Matplotlib flat ORTEP and reports that the visual language changed. Never describe that fallback as equivalent to mesh or ball-and-stick.

## Verification

- Confirm the output exists and is non-empty.
- Decode PNG output; check `%PDF`, `<svg`, or Plotly HTML signatures as appropriate.
- Report camera axis/vector, projection, display mode, style, material, dimensions, backend, and fallback status.