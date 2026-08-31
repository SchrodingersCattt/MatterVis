# Coordination and Packing Polyhedra

For atom-centred coordination, render every matching center in the primary
half-open unit cell by omitting `site` and `sites`:

```bash
mat-vis render INPUT -o OUTPUT.png --backend cpu --json \
  --view unit_cell --style ball --orthogonal \
  --show-cell --show-axes --background '#FFFFFF' \
  --polyhedron '{"center":"Al","ligand":"O","level":"atom"}'
```

Use `ball_stick` when discrete covalent components must retain bonds. Keep the
default center-element hue; do not name a color unless the user requested one.
Omit `cutoff` for the natural shell. A supplied cutoff is a hard radial cap;
`fallback_max` limits coordination count.

Use `site` or `sites` only when the user asks for selected source centers.
Use `instance_overrides` to hide or restyle individual centers:

```json
{
  "center": "Al",
  "ligand": "O",
  "level": "atom",
  "instance_overrides": {
    "0": {"color": "#0072B2"},
    "1": {"visible": false}
  }
}
```

Set `"center_images":true` only when periodic center images are requested.
Ligands may cross the boundary without replicating center polyhedra.

Verify the render JSON center count, coordination numbers, and
`effective_colors`. Confirm the cell and a/b/c axes are visible at delivery
size and that the camera/viewport expose the coordination network without
unnecessary replication. At least four non-coplanar ligands are required for a
hull.
