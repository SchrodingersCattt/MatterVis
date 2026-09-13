# Verification and Delivery

Read this before delivering an image, vector graphic, HTML view, or animation.

## Required checks

- Run the literal installed `mat-vis` command and retain its exit code, stdout,
  and stderr.
- Confirm the output exists, is non-empty, has the requested format, and decodes.
- For PNG, reject an all-background image and inspect the final-size artifact.
- Record input/output hashes when provenance matters.
- Record dimensions, display, representation, shading, camera, projection,
  backend, output hash, and warning classification.
- For per-atom colors, record field source, effective reduction/component,
  exact range and range scope, unit, finite/missing counts, missing color,
  LUT hash, and sidecar manifest hash. Confirm atom-group overrides win and
  bond colors are unchanged.
- For a finite view of periodic topology, also record the crossing bond image
    shifts, periodic translation rank/directions, finite-window policy, number of
    added image atoms/fragments, and confirm that visible endpoints are graphical
    truncations rather than chemical termination.
- For vibration displacement arrows, also verify atom/order mapping, the
    declared centre-versus-tail anchor convention, vector units, fixed scale,
    relative-length preservation, filtering threshold, and global sign caveat.
- Deliver one selected artifact unless another set was requested.

For PDF require `%PDF`; for SVG require an `<svg` root and reject a full-canvas
`<image>` raster substitute; for HTML require Plotly content. These signatures
do not replace visual inspection.

## Requested versus effective

MatterVis does not silently fall back. Preserve CLI output and confirm that the
actual backend, representation, shading, camera, and selection equal the
request. A failed requested backend is a failure, not permission to substitute.

## Visual acceptance

Command success, byte size, and decoding do not prove visual quality. Inspect
clipping, overlap, labels, cell edges, whitespace, and readability at delivery
size. For animation, inspect motion rather than one frame.

For an auditable animation, record a machine-readable manifest with:

- source paths/hashes, resolved source-frame IDs, and frame-to-time/stage map;
- stable-ID field, selected IDs, and topology/selection hash when applicable;
- per-atom array names, shapes, units, and overlay scale;
- requested/effective display, representation, shading, and backend;
- camera, screen-up, projection, shared viewport, canvas, dimensions, and FPS;
- output-specific decoded frame count and duration (GIF and MP4 may differ);
- first/middle/last and extrema QA hashes;
- property reduction/range/unit, finite/missing counts, LUT/manifest hashes,
  and source frames containing property extrema when property color is active;
- objective validation separately from `visual_acceptance`, which starts as
    `pending` until a final-size motion review.

Do not infer a backend from the filename. Preserve structured export facts when
available; otherwise derive the manifest from the literal command, retained
logs, hashes, and decoded outputs.

Models without image inspection report objective checks only and leave visual
acceptance pending. Chemistry and semantic-fatal warnings block delivery.

For chains, layers, and frameworks, verify independently that every displayed
bond is canonical, each crossing bond has the intended nearest image, no
screen-spanning bond was introduced, and no whole-cell replication was added
without an explicit scientific reason.

For multi-panel PNGs, run `skills/visualize-materials/scripts/check_panel_layout.py`
with explicit panel boundaries (or `--panels` only for truly equal-width final
cells). Report each panel's ink bounding box, occupancy, and four safety pads.
The defaults are 70–95% bounding-box occupancy and 24 px minimum pad; override
them with CLI arguments when the figure class requires it and record the reason.
