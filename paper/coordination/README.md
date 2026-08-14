# Coordination Topology Figures

Final publication figures in this folder are CLI artifacts. Direct Python API
calls are useful for unit tests and renderer development, but they are not the
reproducible delivery path.

## Garnet dense-coordination figure

```bash
PYTHONPATH=. python -m crystal_viewer render /path/to/garnet.cif \
  -o garnet-publication.png --view unit_cell --publication-layout \
  --polyhedron '{"id":"o8","center":"Gd","ligand":"O","level":"atom","fallback_max":8}' \
  --polyhedron '{"id":"o6","center":"Zr","ligand":"O","level":"atom","fallback_max":6}' \
  --polyhedron '{"id":"o4","center":"Al","ligand":"O","level":"atom","fallback_max":4}' \
  --config paper/coordination/garnet-publication-style.json \
  --title 'CaGd2HfSc0.97Cr0.03Al3O12 (Ia-3d, a = 12.4113 A)' \
  --width 2325 --height 1888 --scale 1
```

After installation, `mat-vis render` is the equivalent console entry point.
The committed JSON owns the material, mixed-site sectors, legend, and panel
labels; it intentionally contains no camera. For the verification garnet, the
canonical half-open cell contains 24 O8, 16 O6, and 24 O4 polyhedra.
