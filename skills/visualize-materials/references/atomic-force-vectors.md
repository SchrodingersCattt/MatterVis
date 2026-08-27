# Atom-Centred Forces with Density Fields

Use this path when forces, accelerations, dipoles, or related vectors must be
readable on atoms while a Cube isosurface or another translucent field remains
visible. Also read `diagnose-and-select.md`, `camera.md`, `cpu-static.md`, and
`verification.md`.

## Preserve the physical anchor

For a nuclear force, set every arrow `origin` to the corresponding nuclear
Cartesian position. Leave `tail_offset` at zero. The part inside an atom is
supposed to be occluded; the visible shaft must remain collinear with the atom
centre and the resolved force.

Use one declared `scaled` group when relative force magnitudes matter. Increase
the shared scale until retained arrows emerge beyond the visible atom radii.
Do not shift individual tails, normalize individual arrows, or rotate vectors
for composition. If direction is the only stated quantity, `normalized` mode
is acceptable, but disclose that magnitude is no longer encoded.

```json
[
  {
    "id": "nuclear-forces",
    "magnitude_mode": "scaled",
    "scale": 1.6,
    "viewport_policy": "clip",
    "color": "#A99C50",
    "style": {
      "shaft_radius": 0.10,
      "head_length": 0.32,
      "head_radius": 0.24,
      "sides": 16
    },
    "arrows": [
      {
        "id": "atom-0",
        "origin": [0.0, 0.0, 0.0],
        "vector": [0.4, -0.2, 0.1],
        "metadata": {"units": "eV/angstrom", "source_atom": 0}
      }
    ]
  }
]
```

Absolute `head_length` and `head_radius` make arrowheads consistent across a
group with unequal vector lengths. Keep each head shorter than its displayed
arrow. Use the ratio fields instead when proportional arrowheads are desired.

## Make the field subordinate

Render the structure, field mesh, and vectors in one MatterVis scene so they
share the camera and depth buffer. Do not draw arrows after export.

Choose the isovalue from a small, recorded sweep on the real field. Prefer the
lowest value that preserves the intended density topology without swallowing
bonds or hiding all arrow shafts. Use one fixed isovalue, opacity, grid, camera,
and geometry across an iterative sequence. Raising the isovalue changes the
displayed level set, not the underlying field; record it in provenance.

Use a vector colour distinguishable from every nearby element and field colour.
For the conventional red oxygen / white hydrogen / navy density combination,
a muted olive yellow is usually separable. Check contrast on the final white
background rather than relying on the colour name.

## Camera and case selection

Use an asymmetric oblique camera, not a default equal-axis view. Inspect the
projected angle between each important vector and nearby bonds. If a real force
lies almost on a bond and the scientific purpose is to explain that force and
bond direction are independent, choose another physically valid initial
geometry and recompute the force. Never rotate the computed vector alone.

For a trajectory or SCF sequence, freeze the camera before rendering frame one.
Changing the camera, isovalue, or vector scale between frames is a data-encoding
change, not animation polish.

## Verify before delivery

- Resolve overlays and confirm each origin equals its source atom position.
- Confirm display vectors remain parallel to raw vectors and use one group scale.
- Record raw units, scale, style dimensions, isovalue, opacity, and camera.
- Inspect at delivery size that shafts emerge from atoms, heads are not merely
  isolated caps, and the field does not conceal the molecular geometry.
- For explanatory cases, report vector-to-bond angles from 3D data as well as
  inspecting their projected screen angles.
- For every animation frame, check clipping, camera stability, field continuity,
  arrow visibility, and the active semantic colour.
