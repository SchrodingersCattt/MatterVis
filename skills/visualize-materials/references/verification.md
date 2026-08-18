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

For an auditable animation, record a machine-readable manifest with:

- source paths/hashes, resolved source-frame IDs, and frame-to-time/stage map;
- stable-ID field, selected IDs, and topology/selection hash when applicable;
- per-atom array names, shapes, units, and overlay scale;
- requested/effective display, style, material, backend, and fallback reason;
- camera, screen-up, projection, shared viewport, canvas, dimensions, and FPS;
- output-specific decoded frame count and duration (GIF and MP4 may differ);
- first/middle/last and extrema QA hashes;
- objective validation separately from `visual_acceptance`, which starts as
    `pending` until a final-size motion review.

Do not infer a backend from the filename. Preserve structured export facts when
available; otherwise derive the manifest from the literal command, retained
logs, hashes, and decoded outputs.

Models without image inspection report objective checks only and leave visual
acceptance pending. Chemistry and semantic-fatal warnings block delivery.

For multi-panel PNGs, run `skills/visualize-materials/scripts/check_panel_layout.py`
with explicit panel boundaries (or `--panels` only for truly equal-width final
cells). Report each panel's ink bounding box, occupancy, and four safety pads.
The defaults are 70–95% bounding-box occupancy and 24 px minimum pad; override
them with CLI arguments when the figure class requires it and record the reason.
