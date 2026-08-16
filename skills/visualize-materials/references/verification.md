# Verification and Delivery

Read this before delivering an image, vector graphic, HTML view, or animation.

## Required checks

- Run the literal installed `mat-vis` command and retain its exit code, stdout,
  and stderr.
- Confirm the output exists, is non-empty, has the requested format, and decodes.
- For PNG, reject an all-background image and inspect the final-size artifact.
- Record input/output hashes when provenance matters.
- Record dimensions, display, style, material, camera, projection, backend,
  fallback reason, and warning classification.
- Deliver one selected artifact unless another set was requested.

For PDF require `%PDF`; for SVG require an `<svg` root; for HTML require
Plotly content. These signatures do not replace visual inspection.

## Requested versus effective

A successful fallback is not automatically the requested result. Preserve CLI
output and distinguish requested from effective display, style, material, and
backend. Do not deliver a flat ORTEP fallback as a requested mesh, ball-stick,
stick, or wireframe image.

## Visual acceptance

Command success, byte size, and decoding do not prove visual quality. Inspect
clipping, overlap, labels, cell edges, whitespace, and readability at delivery
size. For animation, inspect motion rather than one frame.

Models without image inspection report objective checks only and leave visual
acceptance pending. Chemistry and semantic-fatal warnings block delivery.
