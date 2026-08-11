# Export Verification and Delivery

Read this before delivering any image, vector graphic, HTML view, or animation.

## Machine checks

- Resolve the intended input and record its hash when provenance matters.
- Confirm the output exists, is non-empty, and has the requested extension.
- Decode PNG; check `%PDF`, an `<svg` root, or Plotly HTML content as relevant.
- Record dimensions, scale, camera, projection, display mode, hydrogen and cell
  visibility, viewport or physical scale, and disorder treatment.
- Preserve stdout/stderr and classify warnings.

## Requested versus effective

Record both sides separately:

```text
requested_display/style/material/backend:
effective_display/style/material/backend:
fallback_reason:
requested_camera:
effective_camera_and_up:
```

Parse command output rather than inferring the backend from the filename. A
valid PNG after Plotly/Kaleido failure may be Matplotlib flat ORTEP and does not
prove that requested mesh, flat-stick, ball-and-stick, or wireframe output
succeeded.

Before `AttachFigure`, require the effective style/material/backend to match the
requested visual language. A fallback ORTEP image must not be attached as the
primary candidate for a mesh, ball-stick, stick, or wireframe request. Repair the
static Plotly runtime and retry, or attach a matching previously verified image;
otherwise deliver Plotly HTML and mark the PNG diagnostic-only.

MatterVis 0.0.0 does not write a native manifest/sidecar and has no
`--effective-backend` or `--no-fallback` flag. Create a caller-owned JSON sidecar
from the command, captured stdout/stderr, input/output hashes, and checks above.
For a successful CLI run without fallback text, intentional backend identity is
not printed explicitly; mark it inferred from the dispatch contract or verify it
through Python.

Python callers can determine the backend without guessing:

```python
from crystal_viewer.render.api import render
result = render(scene, style)
backend = "plotly" if result.plotly_figure is not None else "matplotlib"
```

All combinations except `material="flat"` plus `style="ortep"` dispatch to
Plotly. HTML is always Plotly. Static Plotly export may still fall back in the
CLI, forcing flat ORTEP and orthographic projection.

## Visual acceptance

Command success, byte size, and successful decoding do not prove visual quality.
Inspect the final-size artifact, including comparable scale, clipping, overlap,
labels, cell edges, gutters, and readability. For an animation, inspect motion,
not only one frame.

If the executing model cannot inspect images, report only objective checks. It
must not call an artifact clear, attractive, unclipped, publication-ready, or
visually faithful. Attach it for a human or vision-capable reviewer and mark
visual acceptance pending. Record the reviewer and observed defects or approval
when available.

Chemistry and semantic-fatal warnings block an automatic publication-ready
claim. Export warnings may permit an explicitly disclosed fallback; display
warnings require a diagnostic label.
