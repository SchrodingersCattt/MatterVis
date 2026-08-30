# Static Render

Run the render directly:

```bash
mat-vis render INPUT -o OUTPUT.png --backend cpu --json \
  --orthogonal --background '#FFFFFF'
```

If a documented input format fails to load, report an application failure.
Do not convert it to another format to bypass the error.

MatterVis auto-selects a periodic unit cell with visible cell edges, or a fitted
nonperiodic/synthetic-cell object with no cell. Its automatic camera faces the
largest lattice face. Do not add a camera direction, replication, or cell
override unless the requested evidence needs one.

Use `--style ball_stick` for molecules and covalent networks. Use `--style ball`
for a dense ionic/coordination structure when bonds are not part of the evidence.
LAMMPS numeric types require the provenance-backed full `--type-map`; never
guess elements from type numbers or a model filename.

For a comparison, render both structures with the same camera, projection,
canvas, and physical scale. Use the panel reference only when one composed figure
is requested.

Accept only exit 0 with one JSON result whose backend/output match the command.
Open the final-size image and reject clipping, excessive whitespace, missing
atoms/bonds/cell, or an all-background result. Do not substitute a custom plot.
