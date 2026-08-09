---
name: visualize-materials
description: 'Visualize crystal and molecular material structures with MatterVis. Use when rendering CIF files, making publication figures, choosing crystallographic camera axes, setting display style and whitespace, keeping comparable panels on a shared visual scale, exporting PNG/PDF/SVG/HTML, or producing deterministic trajectory animations and terminal views in headless environments.'
---

# Visualize Materials

Create a deterministic material visualization for a caller who cannot inspect the image. Treat camera, projection, display style, framing, output path, and verification as explicit data rather than visual choices.

## Required defaults

Unless the caller specifies otherwise:

- Use an **orthographic** camera.
- Look from the structure toward **`+c`**; for structures without a lattice, use Cartesian **`+Z`**.
- Use `+b` as screen-up when lattice data exists, otherwise Cartesian `+Y`.
- Use `formula_unit`, `ball_stick`, and `mesh` for the initial image.
- For comparable publication structure panels, prefer one shared camera, viewport, projection, and visual scale. Do not mix `flat` and `mesh` in a comparable row without stating why.
- Prefer a white background, thin charcoal marks, restrained semantic colors, and direct labels. Avoid decorative gradients, rainbow palettes, unnecessary titles, and opaque overlays on the structure.
- Write a deterministic non-interactive companion description with the TUI when practical.
- State the selected view axis, projection, display mode, style, material, image size, output path, and backend in the final response.

Do not use `auto`, `diagonal`, perspective, or an unrecorded interactive camera as the only delivered view. Never ask a caller without vision to judge whether the angle “looks good.”

## Choose a path

Read the matching path document before running commands:

- [Plotly 3D render](./reference/plotly-render.md) — mesh, ball-and-stick, non-ORTEP `flat`, interactive HTML, and normal static image requests.
- [Matplotlib flat ORTEP](./reference/matplotlib-flat-ortep.md) — browser-independent 2D ORTEP publication figures.
- [Publication display and whitespace](./reference/publication-style.md) — journal-style preferences, comparable panels, cropping, gutters, compass placement, and visual-scale contracts.
- [Trajectory and partial-highlight animation](./reference/trajectory-animation.md) — MD trajectory extraction, fixed-camera GIF/MP4 rendering, frame windows, and molecule/fragment highlights.
- [Terminal TUI](./reference/tui.md) — deterministic textual or structured descriptions; not image export.

`flat` is not a general backend name: only `material=flat` plus `style=ortep` selects Matplotlib. Prefer Plotly/Kaleido for the requested style. If it falls back to Matplotlib ORTEP, report that the visual language changed.

## Procedure

1. Verify the input path exists and note its extension.
2. Identify the output role: single structure, comparable panel, publication composition, trajectory animation, or terminal description.
3. Inspect available commands with `python -m crystal_viewer render --help`; do not assume `matvis` is installed on `PATH`.
4. Read the selected path document and set camera, projection, display mode, style, and visual scale explicitly.
5. Export with explicit width, height, scale, frame range, or stride as applicable.
6. Check that the output exists, is non-empty, and has the requested format. Do not infer quality from command success alone.
7. For an accessible companion, emit structured terminal output using the same named axis when supported.
8. Report any fallback, warnings, hidden hydrogens, cropping, scale changes, or style changes.

## Parameter rules for callers without vision

- Replace subjective requests such as “best angle” with a named lattice axis plus a stated up direction.
- If the requested feature is planar, use its known normal as `--view-direction`; otherwise use `--camera-axis c`.
- Use reciprocal axes (`a*`, `b*`, `c*`) only when viewing lattice planes by their normals is intended.
- `--camera-position` and `--view-direction` are mutually exclusive. `--camera-axis` is also mutually exclusive with both.
- All direction/position vectors must contain three finite values and must be non-zero.
- `--camera-up` is orthogonalized against the view direction; a parallel hint is replaced deterministically.
- `--camera-distance` must be positive. It controls eye distance for axis/direction views, not object coordinates.
- In orthographic projection, distance does not introduce perspective foreshortening.
- Use `--show-hydrogen` explicitly when hydrogen visibility matters; the image CLI hides hydrogen by default.
- Do not launch `serve` unless the caller asks for an interactive browser viewer.
- When a figure is composed from multiple structural renders, use a shared world viewport or an equivalent explicit scale contract; do not fit each panel independently.
- Treat renderer whitespace and paper/compositor whitespace as separate quantities. Crop only background border whitespace, preserve aspect ratio, and do not use cropping to hide a camera or geometry problem.
- For animations, keep the canvas, camera, projection, viewport, object scale, color semantics, and time mapping fixed across frames; select a frame interval and stride explicitly.

## Environment and fallback

Do not assume Chrome exists. Follow the selected path document. The actual static export is decisive because Kaleido versions differ. Preserve the original error and identify any fallback backend and style change.

## Verification checklist

- Input resolved to the intended file.
- Output extension is one of PNG, PDF, SVG, or HTML.
- Projection is recorded and defaults to orthographic.
- Camera is reproducible from axis or numeric vectors.
- Output file exists and has non-zero size.
- PNG can be decoded; PDF begins with `%PDF`; SVG contains an `<svg` root; HTML contains Plotly content.
- Backend and any fallback are reported.
- Structured TUI companion is saved or its omission is explained.
