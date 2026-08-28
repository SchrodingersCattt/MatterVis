# Vibration Mode Vectors

Use this path for phonon or molecular-vibration displacement arrows. Also read
`diagnose-and-select.md`, `camera.md`, and `verification.md`.

## Input contract

Convert any source format into:

- equilibrium Cartesian positions with shape `(N, 3)`;
- Cartesian mode displacements with shape `(N, 3)` in the same atom order;
- stable atom IDs, mode index, frequency, displacement units, and structure;
- the cell and periodicity when the structure is periodic.

Fail on atom-count, order, stable-ID, coordinate-space, or unit ambiguity.
MatterVis renders these arrays; parsing a simulation-code format belongs in an
adapter outside the renderer.

## Arrow placement

Use public native world-space `vector_overlays`; these arrows rotate with the
structure and participate in 3D occlusion. Keep origins and endpoints in the
input structure's source Cartesian frame; MatterVis applies any nonperiodic
synthetic-cell translation internally. For a vibration, set `anchor: center`
so the equilibrium atom lies at the midpoint of the displayed displacement.

Dipoles, forces, and polarization vectors normally start at their physical
anchor instead. Centring is a caller policy, not a separate mesh primitive:
changing `origin` (or an equivalent signed `tail_offset`) keeps the API
adjustable for either convention.

## Static CPU CLI

After applying the scale and centring above, save the arrows as a JSON list:

```json
[
  {
    "id": "mode",
    "magnitude_mode": "absolute",
    "anchor": "center",
    "viewport_policy": "include",
    "color": "#D55E00",
    "style": {
      "shaft_radius": 0.045,
      "head_radius_ratio": 1.8,
      "head_length_ratio": 0.20,
      "sides": 16
    },
    "arrows": [
      {"id": "atom-0", "origin": [0.0, 0.0, 0.0], "vector": [0.2, 0.0, 0.0]}
    ]
  }
]
```

```bash
mat-vis render equilibrium.xyz -o mode.png --backend cpu --json \
  --vector-overlays mode-vectors.json --style ball_stick --orthogonal
```

Use `absolute` because the caller already applied the visual scale. This static
path needs neither Plotly nor Chrome. Keep the shaft distinctly thinner than
ordinary bonds and the arrowhead short enough that vectors do not obscure atom
colours or local bonding.


## Scale and selection

- Preserve relative vector lengths within a mode.
- Use one fixed scale across compared modes, pressures, panels, or frames.
- State that display length is a visual amplification, not thermal amplitude.
- As a first visual candidate, make the longest displayed vector about 15--25%
  of the selected scene diagonal; then inspect the final-size image.
- If centred vectors remain buried by atom spheres, reduce the context atom
  scale before changing the physical anchor convention.
- Apply a shared amplification at either group level or in the prepared
  vectors. Do not repeat the same scale at both group and arrow level.
- If arrows are filtered, use a declared relative threshold such as
  `norm(q_i) / max(norm(q)) >= eta`; hidden arrows do not imply zero motion.
- Record raw units, scale, maximum display length, and `eta` in provenance.

## Semantics

- A normal-mode eigenvector and its global sign reversal are the same mode.
- Infer in-phase/antiphase motion only from relative signs within one mode.
- Match calculations with absolute overlaps or mode-subspace methods rather
  than requiring identical absolute arrow directions.
- Keep stable atom identity through subsetting and periodic image generation.
- Unwrap complete molecules from trusted topology and minimum-image vectors;
  never reconstruct them from screen proximity.
- Use the same global MolCrysKit `bond_scale` for topology, unwrapping, and
  displayed bonds.

## Presentation and verification

Use one arrow colour by default; atom colours retain element identity. Optional
focus styling may subordinate context as wireframe, but the normal MatterVis
style is valid when no focus hierarchy is needed. Prefer a documented lattice
axis or explicit camera that exposes the relevant motion.

Deliver the static CPU figure first. Animated vector overlays are not supported
by this CLI path; if animation is explicitly required, report that boundary
without switching to private code or installing Plotly/Chrome.

Verify atom/order mapping, centred arrow endpoints, relative lengths, maximum
display length, complete periodic fragments, bond topology, camera, clipping,
and final-size readability. Save a manifest containing input provenance, mode
metadata, selected IDs, vector units/scale/filter, camera, and output hash.
