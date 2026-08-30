# Vibration Mode Vectors

Prepare equilibrium Cartesian positions and Cartesian displacements with shape
`(N,3)` in the same atom order. For an ORCA `.hess`, mode `m` is
`L[:, m].reshape(N, 3)`, matched to `$vibrational_frequencies`; do not use
`L[m, :]`.

Keep arrow origins at the source-frame equilibrium atom coordinates. MatterVis
handles synthetic-cell translation. For vibrations use `"anchor":"center"`, so
the visible arrow midpoint is the atom; do not pre-shift origins.

Write one overlay JSON:

```json
[
  {
    "id": "mode",
    "magnitude_mode": "absolute",
    "anchor": "center",
    "viewport_policy": "include",
    "color": "#0072B2",
    "style": {
      "shaft_radius": 0.030,
      "head_radius_ratio": 2.15,
      "head_length_ratio": 0.22,
      "sides": 16
    },
    "arrows": [
      {"id": "atom-0", "origin": [0.0, 0.0, 0.0], "vector": [0.2, 0.0, 0.0]}
    ]
  }
]
```

Then render:

```bash
mat-vis render equilibrium.xyz -o mode.png --backend cpu --json \
  --vector-overlays mode-vectors.json --style ball_stick --orthogonal \
  --no-cell --atom-scale 0.70 --bond-radius 0.10
```

For a periodic mode, keep the real cell and use `--show-cell`. Scale all vectors
once so the longest is about 15--25% of the scene diagonal; preserve relative
lengths. Start by omitting arrows below 10% of the maximum only when weak motion
is not evidence. Use one scale across compared modes.

Verify atom/order mapping, centered arrows, visible shafts and heads, relative
lengths, bonds, camera, clipping, and whitespace. Report the visual amplification
and remember that a mode and its global sign reversal are equivalent.
