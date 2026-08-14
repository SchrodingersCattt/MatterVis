# Coordination Topology Figures

Final publication figures in this folder are CLI artifacts. Direct Python API
calls are useful for unit tests and renderer development, but they are not the
reproducible delivery path.

## Dense-coordination publication figure

All reusable style selection and overrides travel through `mat-vis` arguments.
No project-specific configuration file is required or committed.

```bash
mat-vis render structure.cif \
  -o figure.png --view unit_cell --publication-layout \
  --publication-preset dense_coordination \
  --polyhedron '{"id":"cn8","center":"M8","ligand":"X","level":"atom","fallback_max":8}' \
  --polyhedron '{"id":"cn6","center":"M6","ligand":"X","level":"atom","fallback_max":6}' \
  --polyhedron '{"id":"cn4","center":"M4","ligand":"X","level":"atom","fallback_max":4}' \
  --publication-option materials.8.main.fill=#4CB17A \
  --publication-option materials.8.main.alpha=0.26 \
  --publication-option materials.6.main.fill=#8F50C2 \
  --publication-option materials.6.main.alpha=0.30 \
  --publication-option materials.4.main.fill=#3D90CE \
  --publication-option materials.4.main.alpha=0.34 \
  --publication-option materials.8.main.edge_alpha=0.14 \
  --publication-option materials.6.main.edge_alpha=0.14 \
  --publication-option materials.4.main.edge_alpha=0.14 \
  --publication-option lines.main_edge_width=0.12 \
  --publication-option lines.main_spoke_width=0 \
  --publication-option lines.main_spoke_alpha=0 \
  --publication-option lighting.polyhedron_ambient=0.45 \
  --publication-option lighting.polyhedron_diffuse=0.55 \
  --publication-option materials.8.panel.fill=#41F288 \
  --publication-option materials.6.panel.fill=#A352D7 \
  --publication-option materials.4.panel.fill=#4E92D8 \
  --publication-option atoms.ligand_color=#FF6363 \
  --publication-option atoms.gloss_color=#FFF7F7 \
  --publication-option atoms.sphere_ambient=0.72 \
  --publication-option atoms.sphere_diffuse=0.28 \
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
committed as part of this generic rendering style. Any additional preset field
can be overridden by repeating `--publication-option PATH=VALUE`.
