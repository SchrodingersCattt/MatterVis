# MatterVis Quickstart

Use this path for the normal structure-to-static-image request. It uses the CPU
backend and requires only base MatterVis.

## 1. Inspect and preflight

```bash
mat-vis inspect INPUT.cif --json
mat-vis render INPUT.cif -o OUTPUT.png --backend cpu --check --json
```

The first command reports bounded source and structure metadata. The second
resolves dependencies without loading the input or writing a file. If it is not
available, run only the exact `install` command it reports; see
`capabilities-and-install.md`.

Choose the displayed object from scientific intent:

- one formula unit: `formula_unit`;
- periodic packing: `unit_cell`;
- crystallographic-site diagnosis: `asymmetric_unit`;
- an already finite nonperiodic input: `cluster`.

`cluster` is not a crop or neighbour-shell selector.

## 2. Render explicitly

Formula-unit PNG:

```bash
mat-vis render INPUT.cif -o OUTPUT.png --backend cpu --json \
  --view formula_unit --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --no-hydrogen --no-cell --no-labels \
  --background '#FFFFFF' --atom-scale 1.0 --bond-radius 0.15 \
  --width 1200 --height 900 --scale 1
```

Periodic SVG:

```bash
mat-vis render INPUT.cif -o OUTPUT.svg --backend cpu --json \
  --view unit_cell --style ball_stick --material mesh \
  --camera-axis c --orthogonal --show-cell --no-labels \
  --background '#FFFFFF' --atom-scale 0.85 --bond-radius 0.12 \
  --width 1600 --height 1200 --scale 1
```

Use PDF/SVG when downstream editing needs true vector geometry. Do not request
Plotly or Kaleido for ordinary static output.

## 3. Verify

Require exit 0 and one JSON object on stdout. Confirm that `backend` is `cpu`,
the output hash matches the file, PNG decodes or PDF/SVG has the correct root,
and no chemistry warning blocks delivery. Inspect the final-size artifact; a
valid file and nonzero byte count do not prove visual quality.
