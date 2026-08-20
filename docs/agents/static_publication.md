# Static Publication Figures

The agent-facing MatterVis CLI produces one explicitly specified view per
output. Base MatterVis provides true-vector PDF/SVG and depth-tested PNG through
the CPU backend. It does not expose the historical private publication
compositor.

Legacy `--publication-layout`, `--publication-preset`, `--publication-style`,
`--publication-option`, `--title`, and `--subtitle` flags fail explicitly in
the backend-neutral command. They are not accepted and ignored. Render each
panel separately, verify it, and compose panels in a separate authorized
document or graphics step.

## Coordination-polyhedron panel

Use the base CPU renderer and the public polyhedron JSON contract:

```bash
mat-vis render structure.cif -o coordination.svg --backend cpu \
  --view unit_cell --camera-axis c --orthogonal \
  --polyhedron '{"id":"cn8","center":"M8","ligand":"X","level":"atom","fallback_max":8}' \
  --width 1600 --height 1200 --scale 1 --json
```

Run the same command with `--check` before rendering. Replace placeholder
species at the call site. Repeat `--polyhedron` for additional shells or render
isolated environments as separate outputs. Polyhedra, ORTEP, rings, PNG, PDF,
and SVG are all base capabilities; no Plotly, Kaleido, or Web extra is needed.

## Comparable panels

When externally composing several verified outputs, record one of these
contracts:

- shared physical scale: identical orthographic camera, target, screen-up,
  viewport scale, canvas pixel density, atom/bond ratio, and palette;
- independent fit: each panel is fitted separately and apparent sizes are
  explicitly declared non-comparable.

Do not alter coordinates, atom radii, or x/y aspect ratio to hide whitespace.
Crop only contiguous paper background with a recorded safety margin. Keep PDF
and SVG as vectors; an embedded full-canvas PNG is not accepted as vector
output.

## Acceptance

1. Confirm the CLI result reports `backend: cpu`, output hash, camera, source,
   warnings, and no fallback.
2. Confirm requested polyhedra are present; a non-drawable shell must fail or
   appear as an explicit warning.
3. Confirm SVG contains no full-image `<image>` substitute and PDF/SVG remain
   editable vectors.
4. Inspect every panel at final delivery size before composition.
5. Preserve the individual render JSON manifests with the composed figure.
