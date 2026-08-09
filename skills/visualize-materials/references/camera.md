# Camera Contract

Read this for every image render.

## Defaults

- Use an orthographic camera.
- Look from the structure toward lattice `+c`; without a lattice, use Cartesian
  `+Z`.
- With `--camera-axis c`, omit `--camera-up`: MatterVis uses lattice `+b` as the
  up hint. Pass `--camera-up` only for an explicitly requested Cartesian vector.
- Do not use `auto`, `diagonal`, perspective, or an unrecorded interactive camera
  as the only delivered view.

## Direction controls

Choose exactly one:

- `--camera-axis a|b|c|a*|b*|c*`
- `--view-direction X Y Z`
- `--camera-position X Y Z`

Optional controls include `--camera-up X Y Z` and `--camera-distance D`.
All numeric vectors must be finite and non-zero. Camera distance must be
positive. `--camera-up` is Cartesian and is orthogonalized against the view
direction; a parallel hint is replaced deterministically.

Use reciprocal axes only when a lattice-plane normal is intended. In an
orthographic projection, camera distance does not introduce perspective
foreshortening.

## Multi-panel contract

Comparable structure panels should share camera direction, screen-up,
projection, viewport or physical scale, aspect ratio, and canvas size. Do not
fit each panel independently or silently mix flat and mesh render languages.

## TUI distinction

The static TUI CLI `--view c` is Cartesian `+Z`, not the image CLI's true lattice
`c` for an oblique cell. Use `TerminalViewController.align("c")` when exact
lattice-axis parity is required.

Record requested axis/vectors and the effective camera direction, up vector,
projection, distance, and viewport in the final metadata.
