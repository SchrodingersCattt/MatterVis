# Verification and Delivery

The standard PNG workflow is in `quickstart.md`. Use this page for other formats
or wrapper troubleshooting.

## Verified-render wrapper

Run delivery commands through:

```bash
python scripts/render_verified.py \
  --manifest OUTPUT.manifest.json \
  --crop-padding 24 \
  -- mat-vis render INPUT.cif -o OUTPUT.png ...
```

The wrapper saves adjacent stdout/stderr logs, preserves the CLI exit code,
checks the output signature, rejects blank PNGs, records hashes and geometry,
classifies fallback evidence, and writes a deterministic JSON manifest. Optional
cropping uses the command's actual background color and records the crop without
rescaling. Do not hand-author a sidecar.

For an ordinary single-structure PNG, add `--min-bbox-coverage 0.70`. Lower it
only for a deliberate multi-panel or whitespace-bearing composition.

## Required evidence

Require the manifest to record:

- exact command and CLI exit code;
- input/output/log SHA256;
- output format, byte size, dimensions, and scale;
- foreground count/fraction and bounding box for PNG;
- requested and effective display/style/material/backend;
- camera/projection and visibility arguments;
- fallback reason and warning classification;
- `visual_acceptance=pending` until final-size inspection.

A decoded PNG is insufficient. `blank=true`, mismatched fallback, chemistry
warning, or semantic-fatal warning blocks delivery.

For PDF require `%PDF`; for SVG require an `<svg` root; for HTML require Plotly
content. These signatures do not replace visual inspection.

Models without image inspection report objective checks only. A vision-capable
reviewer must inspect clipping, overlap, labels, cell edges, gutters, and
readability at delivery size.
