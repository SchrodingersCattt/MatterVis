# Plotly 3D Render Path

Read this for ball-and-stick, mesh ORTEP, wireframe, interactive HTML, or any
request that must preserve real 3D geometry. Read `diagnose-and-select.md` and
`camera.md` first, then `verification.md` before delivery.

## Semantics

- `mat-vis render` accepts one CIF input and writes `.png`, `.pdf`, `.svg`, or
  `.html` according to the output suffix.
- `material=mesh` uses Plotly `Mesh3d` atoms and bonds.
- `material=flat` with a non-ORTEP style remains Plotly 3D and uses fixed-screen
  `Scatter3d` primitives.
- The default deliverable is one static PNG. HTML is interactive and does not
  require local Chrome, but generate it only when the caller explicitly requests
  interactive output.
- PNG, PDF, and SVG use Kaleido and may require Chrome/Chromium.

For an admitted small, chemically resolved, low-disorder formula unit, start
with `formula_unit`, `ball_stick`, and `mesh`. Do not apply that recipe when the
diagnosis rejects it.

## Commands

```bash
mat-vis render INPUT.cif -o OUTPUT.png \
  --view formula_unit --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --no-hydrogen --show-cell --show-axes --no-labels \
  --background '#FFFFFF' --atom-scale 1.0 --bond-radius 0.15 \
  --width 1200 --height 900 --scale 2
```

For an ordered crystal above 500 displayed atoms, use the diagnosed large-scene
preset directly:

```bash
mat-vis render INPUT.cif -o OUTPUT.png \
  --view unit_cell --style ball_stick --material mesh \
  --camera-axis c --orthogonal --no-hydrogen --show-cell --no-axes --no-labels \
  --background '#FFFFFF' --atom-scale 0.65 --bond-radius 0.08 \
  --width 1600 --height 1200 --scale 2
```

After a successful render, crop only contiguous near-white paper border with a
24-pixel safety pad. Do not rescale, stretch, rotate, or crop through atoms,
bonds, or cell edges. Because axes are disabled in the large-scene preset, the
crop follows the scientific scene rather than a distant compass anchor. Use
Pillow, already installed with MatterVis:

```bash
python - <<'PY'
from PIL import Image, ImageChops

path = "OUTPUT.png"
image = Image.open(path).convert("RGB")
background = Image.new("RGB", image.size, (255, 255, 255))
bbox = ImageChops.difference(image, background).getbbox()
if bbox:
    pad = 24
    left = max(0, bbox[0] - pad); top = max(0, bbox[1] - pad)
    right = min(image.width, bbox[2] + pad); bottom = min(image.height, bbox[3] + pad)
    image.crop((left, top, right, bottom)).save(path)
PY
```

Only for explicitly requested interactive output:

```bash
mat-vis render INPUT.cif -o OUTPUT.html \
  --camera-axis c --orthogonal
```

Use `--show-hydrogen` explicitly when hydrogens matter; the image CLI hides them
by default. Use `--config` for supported style fields such as disorder treatment.
Live 0.0.0 render choices are:

- views: `formula_unit`, `unit_cell`, `asymmetric_unit`, `cluster`;
- styles: `ball_stick`, `ball`, `stick`, `ortep`, `wireframe`;
- materials: `mesh`, `flat`;
- visibility: hydrogen, cell, axes, labels, and monochrome;
- appearance: atom scale, bond radius, background, dimensions, scale;
- ORTEP probability/mode and JSON view-scoring weights.

## Large scenes

Do not blindly repeat expensive combinations after a warning reports thousands
of expanded atoms or a large distance matrix. Diagnose first, then change the
selection/disorder policy. Lowering scale or switching mesh to flat changes
export cost but does not repair an invalid chemical selection.

## Failure behavior

Let the static command perform the decisive Kaleido check. If unavailable,
MatterVis may fall back to Matplotlib flat ORTEP. Preserve the original error and
never describe the fallback as equivalent to requested mesh, flat-stick,
ball-and-stick, or wireframe output.

The installation reference requires `plotly_get_chrome -y` and a browser
preflight before this command. If a runtime change still makes Chrome disappear,
rerun that exact helper and browser check, then retry the exact render once.
Do not attach the black Matplotlib ORTEP fallback as the primary answer to a
mesh/ball-stick request. If Chrome installation or retry fails, report the static
export failure; do not silently replace a requested PNG with HTML. Generate HTML
only after explicit caller approval.
