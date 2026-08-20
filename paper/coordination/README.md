# Coordination Topology Figures

Generate each structure or coordination panel as a separate verified
MatterVis CPU artifact. The current agent CLI deliberately does not expose the
legacy dense-coordination compositor or any `--publication-*` preset.

```bash
mat-vis render structure.cif -o coordination.svg --backend cpu \
  --view unit_cell --camera-axis c --orthogonal \
  --polyhedron '{"id":"cn8","center":"M8","ligand":"X","level":"atom","fallback_max":8}' \
  --width 1600 --height 1200 --scale 1 --json
```

Replace placeholder species at the call site and run the command once with
`--check` first. Repeat `--polyhedron` when several shells belong in the same
view. For a multi-panel paper figure, render and verify each SVG/PDF separately,
then compose them with an explicitly authorized document or graphics tool.
Keep camera and physical scale identical when apparent sizes are scientifically
comparable, and retain every render JSON manifest with the final figure.
