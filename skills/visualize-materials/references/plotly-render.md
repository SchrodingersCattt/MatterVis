# Plotly 3D Render Path

Read `quickstart.md` first. Use this reference for Plotly-specific limits and
repair, not as a second standard workflow.

- `mesh` uses Plotly `Mesh3d`; non-ORTEP `flat` uses Plotly `Scatter3d`.
- HTML is always Plotly and needs no local Chrome.
- PNG/PDF/SVG use Kaleido and Chrome.
- `material=flat` plus `style=ortep` is the intentional Matplotlib path.

For standard commands, dimensions, camera, and verified delivery, follow
`quickstart.md`. Use `--show-hydrogen` only when hydrogen matters.

## Large scenes

Diagnose the scene before reducing render quality. Lowering scale or switching
material does not repair an invalid chemical selection.

Use `scale=1` for the first large-canvas render and run that actual delivery
through `scripts/render_verified.py`. A zero exit code and nonzero file size do
not rule out an all-white export.

## Config precedence

Config fields represented by ordinary CLI options are overwritten by parser
defaults even when the flag was not written. Put display, style, material,
projection, visibility, colors, dimensions, and scale on the command line.
Use config only for fields with no CLI representation.

## Failure behavior

MatterVis 0.0.2 can fall back from Plotly/Kaleido to Matplotlib flat ORTEP.
That changes the visual language. `scripts/render_verified.py` captures the
fallback text and records the effective backend; do not deliver a fallback as
the requested mesh, ball-stick, stick, or wireframe image.

If Chrome disappears, preserve the real render error and follow `install.md`.
Generate HTML only after an explicit interactive-output request.
