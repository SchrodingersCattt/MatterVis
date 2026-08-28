# Atom- and Molecule-Centred Polyhedra

Read this when the requested structure image includes coordination polyhedra,
packing-shell hulls, atom-centred ligand cages, or molecule-centred neighbour
shells. MatterVis delegates shell chemistry, PBC identity, and shape
classification to MolCrysKit.

## Static CLI

Polyhedra are part of the base CPU renderer; no Plotly/Web extra is required.

`mat-vis render` accepts repeatable `--polyhedron` JSON objects. Required keys are
`center` and `ligand`.

Atom-centred coordination polyhedron:

```bash
mat-vis render INPUT.cif -o OUTPUT.png --backend cpu \
  --view unit_cell --style ball --shading smooth \
  --orthogonal --show-cell --show-axes \
  --cell-width 2 \
  --polyhedron '{"center":"Pb","ligand":"I","level":"atom","fallback_max":6}' \
  --polyhedron-cutoff 3.5
```

Molecule-centred packing shell:

```bash
mat-vis render INPUT.cif -o OUTPUT.png --backend cpu \
  --view unit_cell --style ball_stick --shading smooth \
  --orthogonal \
  --polyhedron '{"center":"C6N2","ligand":"ClO4","level":"molecule","center_kind":"heavy_centroid","color":"#3366cc"}' \
  --polyhedron-cutoff 10.0
```

Repeat `--polyhedron` for multiple overlays. Optional JSON keys are `id` (or
its alias `spec_id`), `color`, `opacity`, `edge_opacity`, `level`,
`center_kind`, `enforce_enclosure`, `centroid_offset_frac`, `cutoff`,
`hard_cutoff`, `fallback_max`, `site` or `sites`, `center_images`, and
`instance_overrides`. Unknown or conflicting alias keys fail explicitly. At
atom level, `site`, `sites`, and `--polyhedron-site` select source atom
indices. At molecule level they select source fragment indices.

## Semantics

- For a periodic overview, request both `--show-cell` and `--show-axes`,
  then confirm the cell and a/b/c compass are visible at delivery size.
- Use `ball_stick` by default for molecules, clusters, and discrete covalently
  bonded components. For a pure dense extended coordination network, use
  `--style ball` with polyhedra so redundant network bonds do not bury the hull.
- For a mixed scene, use MatterVis's public `atom_groups` and `bond_groups` API
  to keep discrete covalent components as ball-stick while suppressing only
  redundant extended-network bonds. Preserve MolCrysKit canonical bonds; do not
  replace the figure with an ad hoc plot. CLI selector support is tracked in
  MatterVis issue #113.

- Atom level uses element symbols. `cutoff` is the hard radial cap and an
  explicit `hard_cutoff` or `center_kind` is rejected as molecule-only.
  One center/ligand rule draws every matching source center in the primary
  half-open unit cell. Ligands may cross a periodic boundary; duplicate center
  images are excluded unless `"center_images": true` is explicit.
- Use `site` or `sites` to draw one or a source-index subset. Use
  `instance_overrides` only for per-center paint or visibility while keeping
  the all-center rule. Atom-level override keys are source atom indices;
  molecule-level keys are center labels:

  ```json
  {"center":"Ti","ligand":"O","level":"atom",
   "instance_overrides":{"0":{"color":"#0072B2"},"1":{"visible":false}}}
  ```

- The default hull hue follows the centre element; face lighting varies
  lightness while preserving that hue.
- Molecule level uses compact molecular formulas and canonical
  `source_molecule_index`. `center_kind` may be `centroid`, `com`, or
  `heavy_centroid`. The normal `cutoff` is the candidate search radius for the
  natural gap/enclosure shell; positive `hard_cutoff` opts into fill-the-ball
  semantics.
- `fallback_max` bounds the chosen coordination number on either level.
- At least four non-coplanar ligand points are required for a drawable hull. The
  CLI exits non-zero rather than silently producing an image without the
  requested polyhedron.
- Before captioning, read the render JSON `polyhedra` summary. Report its
  effective center count and do not name a color unless it appears in
  `effective_colors`.

For custom Python automation, pass MolCrysKit-derived `topology_data` through
`mat_viewer.agent.render`; do not call a Web normalizer or rebuild shells/PBC
images from screen proximity. Follow `verification.md` for static exports and
requested-versus-effective backend checks.
