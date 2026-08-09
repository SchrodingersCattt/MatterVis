# Terminal TUI Path

Use this path for a deterministic textual view, an agent-readable structure summary, or environments where no image is required. It is independent from Plotly and Matplotlib.

## Modes

- Interactive: `python -m crystal_viewer tui INPUT.cif`
- Deterministic text: add `--no-interaction`
- Agent-readable data: add `--format structured`

The TUI is not an image-export backend and must not be described as a fallback PNG/PDF renderer.

## CLI semantic limits

The static CLI's `--view c` is a Cartesian `+Z` preset, not the image CLI's
true lattice-`c` direction. Do not call it the "same named axis" for an oblique
cell. The programmatic `TerminalViewController.align("c")` does use the real
lattice axis; use that when exact camera parity is required.

Structured output serializes per-atom coordinates and adjacency. Emit it
automatically only for a visible scene of at most 200 atoms. For larger scenes,
prefer a bounded count/warning summary; if no summary interface is available,
omit the companion and state why rather than producing a giant dump.

## Accessible defaults for admitted small scenes

For non-interactive delivery, specify:

- `--view c`
- `--projection orthographic`
- an explicitly diagnosed display mode; do not assume formula-unit parity
- `--format structured`
- `--width` and `--height` when fixed terminal framing matters

Avoid `--view auto` or `diagonal` when reproducibility matters.

## Camera controls

Named views:

- `--view a|b|c|diagonal|ab|ac|bc`

Angle controls that override the named view:

- `--azimuth DEG`
- `--elevation DEG`
- `--roll DEG`

Other controls:

- `--projection orthographic|perspective`
- `--zoom FACTOR`
- `--center ATOM_LABEL`
- `--center F1,F2,F3` for fractional coordinates

The TUI does not currently expose reciprocal-axis names or Cartesian camera position through its CLI. Use the Plotly/Matplotlib image CLI when those controls are required.

## Commands

Structured companion:

    python -m crystal_viewer tui INPUT.cif --no-interaction \
      --format structured --display formula_unit \
      --view c --projection orthographic

Fixed-size terminal frame:

    python -m crystal_viewer tui INPUT.cif --no-interaction \
      --view c --projection orthographic \
      --width 100 --height 40

## Verification

- Confirm stdout is non-empty.
- For structured output, confirm it includes structure and camera data.
- Record display mode, view, projection, zoom, dimensions, and visibility toggles.
- State whether the view is Cartesian CLI `+Z` or a controller-aligned lattice axis.
- Use this output as an accessible companion to an image when practical.