# Coordination and Packing Polyhedra

Read this only when the requested image contains atom-centred coordination
polyhedra or molecule-centred packing hulls. The base CPU renderer supports both.

## Atom-centred coordination

Element centres require `"level":"atom"`:

```bash
mat-vis render INPUT -o OUTPUT.png --backend cpu --json \
  --view unit_cell --style ball --orthogonal \
  --show-cell --show-axes --background '#FFFFFF' \
  --polyhedron '{"center":"Ti","ligand":"O","level":"atom"}'
```

For molecules or discrete covalent components, change `--style ball` to
`ball_stick`. Keep the default centre-element hue unless the user requests a
specific color; explicit colors use `#RRGGBB`.

## Selection semantics

- Without `site` or `sites`, one atom rule draws every matching source centre
  in the primary half-open unit cell. Ligands may cross periodic boundaries.
- `site`, `sites`, or `--polyhedron-site` selects source atom indices.
- Periodic centre images are excluded unless `"center_images":true` is explicit.
- Omit `cutoff` for the natural shell. At atom level an explicit `cutoff` is a
  hard radial cap; `fallback_max` bounds the coordination number.
- `instance_overrides` changes visibility or paint for individual source centres:

```json
{"center":"Ti","ligand":"O","level":"atom",
 "instance_overrides":{"0":{"color":"#0072B2"},"1":{"visible":false}}}
```

## Molecule-centred packing

Use compact molecular formulas and an explicit molecule level:

```bash
mat-vis render INPUT -o OUTPUT.png --backend cpu --json \
  --view unit_cell --style ball_stick --orthogonal \
  --show-cell --show-axes --background '#FFFFFF' \
  --polyhedron '{"center":"C6N2","ligand":"ClO4","level":"molecule","center_kind":"heavy_centroid"}'
```

At molecule level, `site` and `sites` select source fragment indices.
`center_kind` may be `centroid`, `com`, or `heavy_centroid`; a positive
`hard_cutoff` opts into fill-the-ball behavior.

## Acceptance

- Require exit 0 and retain the render JSON.
- Check `polyhedra` for centre count, coordination numbers, and `effective_colors`.
- Do not name or describe a polyhedron color unless requested; if requested, quote
  `effective_colors` exactly.
- Confirm the requested cell and a/b/c axes are visible at delivery size.
- If no centre is found, first compare `mat-vis inspect INPUT --json` with
  `center`, `ligand`, and `level`; do not inspect package internals.
- At least four non-coplanar ligand points are required for a hull.
