# Atom- and Molecule-Centred Polyhedra

Read this when the requested structure image includes coordination polyhedra,
packing-shell hulls, atom-centred ligand cages, or molecule-centred neighbour
shells. MatterVis delegates shell chemistry, PBC identity, and shape
classification to MolCrysKit.

## Static CLI

`mat-vis render` accepts repeatable `--polyhedron` JSON objects. Required keys are
`center` and `ligand`.

Atom-centred coordination polyhedron:

```bash
mat-vis render INPUT.cif -o OUTPUT.png \
  --view unit_cell --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --polyhedron '{"center":"Pb","ligand":"I","level":"atom","fallback_max":6,"color":"#ff6600"}' \
  --polyhedron-cutoff 3.5
```

Molecule-centred packing shell:

```bash
mat-vis render INPUT.cif -o OUTPUT.png \
  --view unit_cell --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --polyhedron '{"center":"C6N2","ligand":"ClO4","level":"molecule","center_kind":"heavy_centroid","color":"#3366cc"}' \
  --polyhedron-cutoff 10.0
```

Repeat `--polyhedron` for multiple overlays. Optional JSON keys are `name`,
`color`, `level`, `center_kind`, `enforce_enclosure`,
`centroid_offset_frac`, `hard_cutoff`, and `fallback_max`.
`--polyhedron-site INDEX` selects the displayed fragment used as the primary
analysis anchor; without it, the CLI chooses the first matching fragment.

## Semantics

- Atom level uses element symbols. `cutoff` is the hard radial cap and
  `hard_cutoff` is ignored. A centre element inside a polyatomic fragment is
  supported; when the fragment contains multiple atoms of that element,
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

## Python path

For custom automation, call `analyze_topology(...)` and pass the result to
`build_figure(scene, style, topology_data=result)`. Do not rebuild shells or PBC
images from screen proximity.

Run static exports through `scripts/render_verified.py` and follow
`verification.md` for requested-versus-effective backend checks.
