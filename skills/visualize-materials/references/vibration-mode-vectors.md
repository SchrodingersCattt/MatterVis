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

## Arrow contract

Use public native world-space `vector_overlays`; these arrows rotate with the
structure and participate in 3D occlusion. A vibration vector represents motion
about an equilibrium atom, so centre it on that atom by default:

```python
display = scale * displacement
origin = equilibrium_position - 0.5 * display
arrow = {"origin": origin, "vector": display}
```

Dipoles, forces, and polarization vectors normally start at their physical
anchor instead. Centring is a caller policy, not a separate mesh primitive:
changing `origin` (or an equivalent signed `tail_offset`) keeps the API
adjustable for either convention.

## Scale and selection

- Preserve relative vector lengths within a mode.
- Use one fixed scale across compared modes, pressures, panels, or frames.
- State that display length is a visual amplification, not thermal amplitude.
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

An animated GIF is optional. A useful starting point is sinusoidal motion,
`16` frames, `10` fps, and maximum displayed displacement near `0.35 Å`, with
equilibrium-centred eigenvector arrows fixed for reference. These are adjustable
presentation defaults, not scientific constants. Arrow visibility/scale, motion
amplitude, frame count, fps, camera, context style, and GIF inclusion must be
tuned to the structure and communication goal.

For animation, keep equilibrium topology, camera, viewport, canvas, palette,
vector scale, and crop fixed across frames. Determine one union content box from
all title-free frames, apply it to every frame, then add the title; per-frame
autocrop causes visible zoom jitter, while title-first cropping preserves large
Plotly whitespace. Label displayed motion as amplified.

Verify atom/order mapping, centred arrow endpoints, relative lengths, maximum
display length, complete periodic fragments, bond topology, camera, clipping,
and final-size readability. For GIF, also verify decoded frame count, duration,
loop, extrema, fixed crop, and motion at final size. Save a manifest containing
the input provenance, mode metadata, selected IDs, vector units/scale/filter,
camera, animation parameters, and output hash.