# CPU Static PNG/PDF/SVG

Read this for the default browser-independent static path. Base MatterVis draws
atoms, bonds, thermal ellipsoids, aromatic rings, unit cells, and polyhedra with
one camera and backend-neutral geometry.

```bash
mat-vis render INPUT.cif -o OUTPUT.svg --backend cpu --json \
  --view formula_unit --style ortep --material flat \
  --ortep-mode ortep_hatch --missing-adp-policy error \
  --aromatic-rings bonds --camera-axis c --orthogonal \
  --width 1200 --height 900 --scale 2
```

Explicit aromatic circle and explicit missing-ADP placeholder (only when the
caller requests those semantics):

```bash
mat-vis render INPUT.cif -o OUTPUT.svg --backend cpu --json \
  --style ortep --material flat --ortep-mode ortep_axes \
  --aromatic-rings circle --missing-adp-policy sphere
```

- PNG uses a per-pixel depth buffer and per-pixel transparent fragments.
- PDF/SVG keep triangle, line, arc, hatch, and text geometry as vectors; reject
  an SVG containing a full-canvas `<image>` raster substitute.
- `ortep` is a representation; `flat` is shading; `cpu` is the backend. Do not
  treat those as interchangeable selector names.
- Missing ADPs default to an error. `sphere` is an explicit visual placeholder,
  not a fabricated displacement measurement; record its use.
- Aromatic rings default to ordinary bonds. Select circle/disk only when the
  caller explicitly wants that convention.

The CPU backend never invokes Plotly, Kaleido, Chrome, Dash, or a Web service.
If it fails, report the error; do not change representation or backend.
