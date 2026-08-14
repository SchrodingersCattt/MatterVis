# Coordination Topology Figures

Final publication figures in this folder are CLI artifacts. Direct Python API
calls are useful for unit tests and renderer development, but they are not the
reproducible delivery path.

## Dense-coordination publication figure

Select the layout with `--publication-preset dense_coordination` and the
measured material/lighting profile with `--publication-style blender`. Explicit
`--publication-option PATH=VALUE` arguments are applied last.
No project-specific configuration file is required or committed. CI parses the
command below with the live CLI and publication-option resolver.

```bash
mat-vis render structure.cif \
  -o figure.png --view unit_cell --publication-layout \
  --publication-preset dense_coordination \
  --publication-style blender \
  --polyhedron '{"id":"cn8","center":"M8","ligand":"X","level":"atom","fallback_max":8}' \
  --polyhedron '{"id":"cn6","center":"M6","ligand":"X","level":"atom","fallback_max":6}' \
  --polyhedron '{"id":"cn4","center":"M4","ligand":"X","level":"atom","fallback_max":4}' \
  --publication-site-style M8a,M8b '#86D533,#2F80D9' 1,1 'site A' 0.28 \
  --publication-site-style M6 '#A9B4BE' 1 'site B' 0.28 \
  --publication-legend-entry '#86D533,#2F80D9' 'site A' \
  --publication-legend-entry '#A9B4BE' 'site B' \
  --publication-panel-label cn8 '[M8]X8' \
  --publication-panel-label cn6 '[M6]X6' \
  --publication-panel-label cn4 '[M4]X4' \
  --publication-legend-footer 'coordination colors: CN8 / CN6 / CN4' \
  --width 2325 --height 1888 --scale 1
```

Replace the placeholder centre and ligand symbols at the call site. Titles,
site labels, legend text, and cameras are also call-site data and must not be
committed as part of this generic rendering style. Any preset or style field
can be overridden by repeating `--publication-option PATH=VALUE`.
