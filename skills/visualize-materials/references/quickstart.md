# MatterVis Quickstart

Use this path for the normal structure-to-static-image request. It uses the CPU
backend and requires only base MatterVis.

## 1. Inspect, then render

```bash
mat-vis inspect INPUT --json
```

Inspect reports structure metadata including periodicity and synthetic-cell state.

Choose the displayed object from scientific intent:

- nonperiodic or synthetic-cell input: auto selects `cluster` and hides the cell;
- periodic input: auto selects `unit_cell` and shows the cell;
- one formula unit: `formula_unit`;
- periodic packing: `unit_cell`;
- crystallographic-site diagnosis: `asymmetric_unit`;
- an explicitly finite selection: `cluster`.

Explicit `--view` and `--show-cell`/`--no-cell` override auto. `cluster` is not a
crop or neighbour-shell selector.

## 2. Render explicitly

Nonperiodic molecule or cluster PNG:

```bash
mat-vis render INPUT.xyz -o OUTPUT.png --backend cpu --json \
  --style ball_stick --shading smooth --orthogonal \
  --background '#FFFFFF' --atom-scale 1.0 --bond-radius 0.15 \
  --width 1200 --height 900 --scale 1
```

Formula-unit PNG:

```bash
mat-vis render INPUT.cif -o OUTPUT.png --backend cpu --json \
  --view formula_unit --style ball_stick --shading smooth \
  --orthogonal --no-cell --no-labels \
  --background '#FFFFFF' --atom-scale 1.0 --bond-radius 0.15 \
  --width 1200 --height 900 --scale 1
```

Periodic SVG:

```bash
mat-vis render INPUT.cif -o OUTPUT.svg --backend cpu --json \
  --view unit_cell --style ball_stick --shading smooth \
  --orthogonal --show-cell --no-labels \
  --background '#FFFFFF' --atom-scale 0.85 --bond-radius 0.12 \
  --width 1600 --height 1200 --scale 1
```

Projected 2D PNG (no lighting or 3D mesh rasterization):

```bash
mat-vis render INPUT.cif -o OUTPUT.png --backend matplotlib --json \
  --view unit_cell --style ball_stick \
  --orthogonal --show-cell --no-labels \
  --background '#FFFFFF' --atom-scale 0.85 --bond-radius 0.12 \
  --width 1600 --height 1200 --scale 1
```

Use PDF/SVG when downstream editing needs true vector geometry. Do not request
Plotly or Kaleido for ordinary static output.

## 3. Verify

Require exit 0 and one JSON object on stdout. Confirm that `backend` is the
explicitly requested `cpu` or `matplotlib`,
the output hash matches the file, PNG decodes or PDF/SVG has the correct root,
and no chemistry warning blocks delivery. Inspect the final-size artifact; a
valid file and nonzero byte count do not prove visual quality.
