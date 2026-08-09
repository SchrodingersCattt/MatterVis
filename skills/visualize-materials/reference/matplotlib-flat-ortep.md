# Matplotlib Flat ORTEP Path

Use this path for browser-independent, camera-projected 2D ORTEP publication output.

## Exact dispatch

The Matplotlib backend is selected only by both:

- `--material flat`
- `--style ortep`

`material=flat` alone does not select Matplotlib. With ball, ball-and-stick, stick, or wireframe, it remains Plotly 3D `Scatter3d`.

## Capabilities and limits

- Uses Matplotlib's non-interactive `Agg` backend.
- Does not require Chrome or a browser.
- Exports PNG, PDF, and SVG.
- Uses camera-projected thermal ellipsoids and an orthographic projection.
- Is not an equivalent fallback for Plotly mesh, ball-and-stick, or interactive HTML.

Choose it directly when publication ORTEP or browser-independent static output is the requirement. Otherwise prefer the requested Plotly style and allow the CLI to report a fallback if needed.

## Camera controls

Use the same explicit camera contract as Plotly:

- `--camera-axis c` by default
- `--view-direction X Y Z` for a known Cartesian normal
- `--camera-position X Y Z` for an explicit eye direction
- `--camera-up X Y Z` for screen orientation
- `--orthogonal`

The renderer consumes the resulting scene `view_direction` and `up`, so these controls affect the 2D projection as well as Plotly.

## Command

    python -m crystal_viewer render INPUT.cif -o OUTPUT.pdf \
      --view formula_unit --style ortep --material flat \
      --camera-axis c --orthogonal \
      --width 1200 --height 900 --scale 2

Use `--show-hydrogen` explicitly if hydrogens must appear; the image CLI hides them by default.

## Verification

- Confirm that command output identifies the requested file.
- Confirm the file exists and is non-empty.
- Decode PNG output, check `%PDF`, or check for an `<svg` root.
- Report `Matplotlib flat ORTEP` as the backend.
- Report the camera axis/vector, screen-up, projection, dimensions, and hydrogen visibility.
- If this path was an automatic fallback, include the Plotly/Kaleido error and state that the visual style changed.