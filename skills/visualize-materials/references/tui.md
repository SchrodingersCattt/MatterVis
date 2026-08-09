# Terminal TUI Path

Read this only for deterministic textual views, agent-readable summaries, or
terminal interaction. The TUI is not an image backend or PNG/PDF fallback.

## Modes

- Interactive: `python -m crystal_viewer tui INPUT.cif`
- Deterministic: add `--no-interaction`
- Agent-readable: add `--format structured`

The static CLI `--view c` is Cartesian `+Z`, not the image CLI's true lattice
`c` for an oblique cell. Use `TerminalViewController.align("c")` when exact
lattice-axis parity is required.

Structured output serializes per-atom coordinates and adjacency. Emit it
automatically only when the visible scene has at most 200 atoms. For larger
scenes, prefer a bounded count/warning summary; if unavailable, omit it and
explain why.

## Small-scene commands

```bash
python -m crystal_viewer tui INPUT.cif --no-interaction \
  --format structured --display <diagnosed-mode> \
  --view c --projection orthographic
```

```bash
python -m crystal_viewer tui INPUT.cif --no-interaction \
  --view c --projection orthographic --width 100 --height 40
```

Named CLI views are `a|b|c|diagonal|ab|ac|bc`; angle overrides are `--azimuth`,
`--elevation`, and `--roll`. Other controls include projection, zoom, and atom or
fractional-coordinate centring. The CLI does not expose reciprocal-axis names or
Cartesian camera positions.

Record display mode, Cartesian versus lattice-aligned view semantics,
projection, zoom, dimensions, and visibility toggles.
