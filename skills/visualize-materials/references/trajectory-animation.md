# Trajectory and Animation

Use the input directly; do not convert a supported trajectory.

Final frame of a periodic trajectory:

```bash
mat-vis render trajectory.dump -o final.png --backend cpu --json \
  --frame -1 --style ball_stick --show-cell --orthogonal \
  --type-map O H
```

Whole periodic animation:

```bash
mat-vis render trajectory.dump -o trajectory.gif --backend cpu --json \
  --style ball_stick --show-cell --orthogonal --fps 12 \
  --type-map O H
```

Omit `--type-map` when elements are already encoded. For nonperiodic or
synthetic-cell input, use `--no-cell`. Use `ball_stick` for molecular and
covalent trajectories; use `ball` only when bonds are not part of the evidence.

Do not add `--view-direction` by default. The automatic camera faces the largest
lattice face and remains fixed across frames. `--frame-range START:STOP:STEP`
uses a half-open slice; `--stride` is applied afterward. Slowing an unchanged
sequence means lowering FPS, not silently dropping frames.

Use stable atom/molecule IDs and the instantaneous cell when a selection or bond
crosses PBC. Do not select by screen position or create screen-spanning bonds.
Keep one physical viewport, property range, style, and type map across the
animation.

Verify the selected frame count, first/middle/last frames, output dimensions,
cell, type identity, bonds, fixed camera, clipping, and motion at delivery size.
Retain the exact frame/range/stride/FPS and render JSON.
