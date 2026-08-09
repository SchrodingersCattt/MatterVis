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
- For `--camera-axis c`, omit `--camera-up`: MatterVis then uses lattice `+b` as the up hint. Pass `--camera-up` only for an explicitly requested Cartesian vector.
- Diagnose structure selection and disorder before choosing a display mode or style. Use `formula_unit`, `ball_stick`, and `mesh` only for a reasonably small, chemically resolved, low-disorder scene.
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
4. Diagnose expanded/raw, selected, major, minor, fragment, and bond counts plus chemistry/parser warnings before choosing the display mode. If the CLI cannot expose those facts, use the documented API or stop with that limitation.
5. Read the selected path document and set camera, projection, display mode, disorder treatment, style, and visual scale explicitly.
6. Export with explicit width, height, scale, frame range, or stride as applicable.
7. Check that the output exists, is non-empty, and has the requested format. Do not infer quality or requested-style success from command success alone.
8. Emit a structured TUI companion only when its camera semantics match and the visible scene is at most 200 atoms; otherwise save a bounded summary or explain the omission.
9. If you cannot inspect images, record only objective checks and request visual review; do not say the image is clear, attractive, unclipped, or publication-ready.
10. Report classified warnings and both requested and effective display/backend settings.

## Display-mode decision

- `formula_unit` is an MCK chemical selection, not a guarantee of one compact molecule. Do not choose it automatically when it exceeds 500 atoms, retains more than 50% of expanded atoms, has more than 25% minor atoms, or emits formula/moiety parsing warnings.
- `asymmetric_unit` deduplicates by label, element, disorder group, and disorder assembly. Use it as a diagnostic reduction for complex/disordered CIFs; it is not raw `_atom_site` and not major-only.
- `unit_cell` provides crystal context and normally adds complete boundary-fragment replicas. Disclose that behavior; the image CLI does not currently expose strict `include_boundary_replicas=False`.
- `cluster` only disables PBC for an already finite input. It does not select a center, radius, molecule, or connected component.
- For a local environment, first extract an auditable target structure or use an API that accepts the intended center/fragment. Do not misuse bare `cluster` as a crop operation.
- High-disorder scenes require an explicit strategy: major-only, hide minor, opacity, or ASU diagnostic. `minor_opacity` does not reduce `outline_rings`; when the CLI cannot express the required policy, use `--config`/API if supported or report the limitation instead of forcing a formal figure.

## Parameter rules for callers without vision

- Replace subjective requests such as “best angle” with a named lattice axis plus a stated up direction.
- If the requested feature is planar, use its known normal as `--view-direction`; otherwise use `--camera-axis c`.
- Use reciprocal axes (`a*`, `b*`, `c*`) only when viewing lattice planes by their normals is intended.
- `--camera-position` and `--view-direction` are mutually exclusive. `--camera-axis` is also mutually exclusive with both.
- All direction/position vectors must contain three finite values and must be non-zero.
- `--camera-up` is a Cartesian vector, not a lattice-axis name. It is orthogonalized against the view direction; a parallel hint is replaced deterministically.
- `--camera-distance` must be positive. It controls eye distance for axis/direction views, not object coordinates.
- In orthographic projection, distance does not introduce perspective foreshortening.
- Use `--show-hydrogen` explicitly when hydrogen visibility matters; the image CLI hides hydrogen by default.
- Do not launch `serve` unless the caller asks for an interactive browser viewer.
- When a figure is composed from multiple structural renders, use a shared world viewport or an equivalent explicit scale contract; do not fit each panel independently.
- For molecule focus or highlighting, use the MolCrysKit-native molecule identity described in the publication path. Load through MatterVis and let its canonical loader supply complete PBC-unwrapped molecules, topology, and boundary copies; never rebuild `draw_atoms`, bonds, molecule membership, or covalent cutoffs in an ad hoc plotting script.
- Keep coordinates, selection, bonds, and display mode under one PBC contract. Wrapped coordinates require wrapped geometry; do not calculate a minimum-image bond and draw it between the unshifted endpoints, which creates a box-spanning bond. Never invent pseudo-elements only to select a render style.
- Treat renderer whitespace and paper/compositor whitespace as separate quantities. Crop only background border whitespace, preserve aspect ratio, and do not use cropping to hide a camera or geometry problem.
- For animations, keep the canvas, camera, projection, viewport, object scale, color semantics, and time mapping fixed across frames; select a frame interval and stride explicitly.

## Environment and fallback

Do not assume Chrome exists. Follow the selected path document. The actual static export is decisive because Kaleido versions differ. Preserve the original error and record `requested_style/material/backend`, `effective_style/material/backend`, and `fallback_reason`; a valid PNG after fallback does not prove the requested visual language succeeded.

## Verification checklist

- Input resolved to the intended file.
- Output extension is one of PNG, PDF, SVG, or HTML.
- Projection is recorded and defaults to orthographic.
- Camera is reproducible from axis or numeric vectors.
- Output file exists and has non-zero size.
- PNG can be decoded; PDF begins with `%PDF`; SVG contains an `<svg` root; HTML contains Plotly content.
- Requested and effective style, material, backend, display mode, camera direction, and up vector are distinguished.
- Warnings are classified as export, display/readability, chemistry, or semantic-fatal; chemistry and semantic-fatal warnings block an automatic publication-ready claim.
- Structured TUI companion is saved or its omission is explained.
- Visual acceptance names the reviewer or remains explicitly pending when the executing model has no image-inspection capability.
