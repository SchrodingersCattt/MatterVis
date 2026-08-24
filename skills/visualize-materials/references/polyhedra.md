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
  --camera-axis c --orthogonal --show-cell --show-axes \
  --cell-width 2 \
  --polyhedron '{"center":"Pb","ligand":"I","level":"atom","fallback_max":6,"color":"#ff6600"}' \
  --polyhedron-cutoff 3.5
```

Molecule-centred packing shell:

```bash
mat-vis render INPUT.cif -o OUTPUT.png --backend cpu \
  --view unit_cell --style ball_stick --shading smooth \
  --camera-axis c --orthogonal \
  --polyhedron '{"center":"C6N2","ligand":"ClO4","level":"molecule","center_kind":"heavy_centroid","color":"#3366cc"}' \
  --polyhedron-cutoff 10.0
```

Repeat `--polyhedron` for multiple overlays. Optional JSON keys are `id` (or
its alias `spec_id`), `color`, `opacity`, `edge_opacity`, `level`,
`center_kind`, `enforce_enclosure`, `centroid_offset_frac`, `cutoff`,
`hard_cutoff`, and `fallback_max`. Unknown or conflicting alias keys fail
explicitly. `--polyhedron-site INDEX` restricts analysis to that displayed
fragment index; without it, the CLI draws every matching displayed fragment.

## Semantics

- For a periodic overview, request both `--show-cell` and `--show-axes`,
  then confirm the cell and a/b/c compass are visible at delivery size.
- Start dense coordination views with `--style ball`; use `ball_stick` only
  when the bond network is part of the requested evidence. Full-cell bonds can
  visually bury the coordination hull even when the geometry is correct.

- Atom level uses element symbols. `cutoff` is the hard radial cap and an
  explicit `hard_cutoff` or `center_kind` is rejected as molecule-only. A
  centre element inside a polyatomic fragment is supported; when the fragment
  contains multiple atoms of that element,
  MolCrysKit/MatterVis currently select the matching record nearest the fragment
  centre rather than an exact atom label.
- Molecule level uses compact molecular formulas and canonical
  `source_molecule_index`. `center_kind` may be `centroid`, `com`, or
  `heavy_centroid`. The normal `cutoff` is the candidate search radius for the
  natural gap/enclosure shell; positive `hard_cutoff` opts into fill-the-ball
  semantics.
- `fallback_max` bounds the chosen coordination number on either level.
- At least four non-coplanar ligand points are required for a drawable hull. The
  CLI exits non-zero rather than silently producing an image without the
  requested polyhedron.

For custom Python automation, pass MolCrysKit-derived `topology_data` through
`mat_viewer.agent.render`; do not call a Web normalizer or rebuild shells/PBC
images from screen proximity. Follow `verification.md` for static exports and
requested-versus-effective backend checks.
