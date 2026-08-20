# CLI Reference

MatterVis renders atomistic structures and trajectories without launching the
browser viewer. Every input format is parsed into the same canonical structure
frame before scene construction.

## Quick start

~~~bash
# Inspect and preflight without writing a file
mat-vis inspect structure.cif --json
mat-vis render structure.cif -o figure.png --backend cpu --check --json

# Static CIF through the base CPU renderer
mat-vis render structure.cif -o figure.png --backend cpu --json

# VASP structure
mat-vis render POSCAR -o figure.png --view unit_cell

# One frame from an ASE trajectory
mat-vis render trajectory.traj --frame 20 -o frame.png

# LAMMPS trajectory with explicit atom-type order
mat-vis render run.dump --type-map O H -o trajectory.gif \
  --frame-range 0:100:2 --fps 12

# Interactive HTML
mat-vis render structure.extxyz -o interactive.html --backend plotly --orthogonal
~~~

## Subcommands

| Command | Description |
|---------|-------------|
| inspect | Report bounded source and structure metadata as JSON |
| capabilities | Resolve requested features to exact optional extras |
| render | Render a structure, one trajectory frame, or a GIF/MP4 animation |
| serve | Launch the interactive Dash browser viewer |
| tui | Open a supported structure or trajectory frame in the terminal |

`inspect`, `capabilities`, and `render --check` are the agent preflight surface.
`--check` resolves requirements only and never creates the output.

---

## render — Structure and trajectory export

~~~
mat-vis render INPUT -o OUTPUT [options]
~~~

### Input and output arguments

| Argument | Description |
|----------|-------------|
| INPUT | Atomistic structure or trajectory path |
| -o, --output | Output path; format is inferred from its extension |
| --input-format FORMAT | ASE format name for an ambiguous filename |
| --type-map ELEMENT ... | Complete LAMMPS atom-type order |
| --frame INDEX | Static frame index; negative indices are accepted |

Supported inputs:

| Input | Reader |
|-------|--------|
| CIF | MolCrysKit renderer-ready public records |
| Cube | Cube parser and volumetric isosurfaces (`[cube]`) |
| POSCAR, CONTCAR, .vasp | ASE VASP reader |
| XYZ, extxyz | ASE, including multi-frame files |
| .traj | ASE trajectory reader |
| LAMMPS dump, lammpstrj | ASE LAMMPS text-dump reader |
| LAMMPS data/configuration | ASE LAMMPS data reader |
| Other ASE-readable files | ASE auto-detection or --input-format |

LAMMPS numeric types are not elements. Pass --type-map whenever the source does
not encode element identity unambiguously; the order is type 1, type 2, and so
on. MatterVis never guesses it from a model filename.

### Supported output formats

| Extension | Format | Backend |
|-----------|--------|---------|
| .png | Raster image | CPU (base) or explicit Plotly + Kaleido |
| .pdf | Vector PDF | CPU (base) or explicit Plotly + Kaleido |
| .svg | Vector SVG | CPU (base) or explicit Plotly + Kaleido |
| .html | Interactive 3D | Plotly (`[plotly]`) |
| .gif | Multi-frame animation | CPU + `[animation]` |
| .mp4 | H.264 animation | CPU + `[animation]` |

Use `--backend cpu|plotly`; there is no backend or representation fallback.
Base MatterVis provides complete CPU PNG/PDF/SVG, ORTEP, rings, polyhedra, and
ordinary structure inputs. Run `mat-vis capabilities --require FEATURE --json`
for an exact install command before using an optional frontend.

### Frames and animations

| Flag | Default | Description |
|------|---------|-------------|
| --frame INDEX | 0 | Select one frame for PNG/PDF/SVG/HTML |
| --frame-range START:STOP[:STEP] | all | Python half-open frame slice for GIF/MP4 |
| --stride N | 1 | Keep every Nth selected animation frame |
| --fps FPS | 12 | Positive animation frame rate |

GIF/MP4 require at least two selected frames. All selected frames use one camera,
canvas, and shared world-space viewport scale.

Animations preserve one requested representation, camera, and CPU backend
across every frame. Plotly GIF/MP4 is rejected explicitly; MatterVis never
substitutes one frame backend for another.

### Display options

| Flag | Default | Description |
|------|---------|-------------|
| --view MODE | auto | CIF defaults to formula_unit; other inputs to unit_cell |
| --style STYLE | ball_stick | ball_stick, ball, stick, ortep, or wireframe |
| --material MAT | mesh | mesh or flat |
| --backend BACKEND | cpu | cpu or plotly; never selected by fallback |
| --orthogonal | yes | Orthographic projection |
| --perspective | no | Perspective projection |

### Camera

Static renders default to a reproducible view from the structure toward `+c`
(`+Z` when no lattice is available), with `+b`/`+Y` pointing up. Camera
direction options are mutually exclusive.

| Flag | Default | Description |
|------|---------|-------------|
| `--camera-axis a|b|c|a*|b*|c*` | `c` | Align to a real or reciprocal lattice axis |
| `--view-direction X Y Z` | — | Cartesian direction from scene toward camera |
| `--camera-position X Y Z` | — | Explicit absolute Cartesian camera position in Å |
| `--camera-up X Y Z` | `+b` / `+Y` | Preferred screen-up direction |
| `--camera-distance D` | `1.8` | Positive scene-fit multiplier for axis/direction views |

`--camera-up` is orthogonalized against the view direction. CPU is the default
static backend and needs neither Chrome nor Kaleido. Explicit Plotly static
export requires `[plotly-export]`; a failure is reported without substitution.

### Visibility toggles

| Flag | Default | Description |
|------|---------|-------------|
| `--show-hydrogen` / `--no-hydrogen` | off | Show/hide hydrogen atoms |
| `--show-cell` / `--no-cell` | on | Show/hide unit cell edges |
| `--show-labels` / `--no-labels` | off | Show/hide atom labels |

`--show-axes`, `--no-axes`, and `--monochrome` are legacy flags not implemented
by the backend-neutral renderer and therefore fail explicitly. They are never
accepted and ignored; the CPU renderer currently emits no lattice-axis
primitive.

### Numeric parameters

| Flag | Default | Range | Description |
|------|---------|-------|-------------|
| `--atom-scale` | 1.0 | 0.3–1.8 | Atom radius scale factor |
| `--bond-radius` | 0.15 | 0.05–0.40 | Bond cylinder radius (Å) |
| `--camera-distance` | 1.8 | > 0 | Scene-fit multiplier (not Å) |
| `--width` | 900 | — | Image width in pixels |
| `--height` | 720 | — | Image height in pixels |
| `--scale` | 2 | 1–4 | Supersampling factor (effective DPI = 72 × scale) |

### Colour and ORTEP

| Flag | Default | Description |
|------|---------|-------------|
| `--background` | `#FFFFFF` | Background hex colour |
| `--ortep-probability` | 0.5 | Ellipsoid probability (0.0–1.0) |
| `--ortep-mode` | `ortep_axes` | ORTEP decoration: `ortep_solid`, `ortep_axes`, or `ortep_hatch` |
| `--aromatic-rings` | `bonds` | `bonds`, `circle`, or `disk` |
| `--missing-adp-policy` | `error` | ORTEP missing-ADP policy: `error` or explicit `sphere` placeholder |

`--style ortep` selects the representation. `--material mesh|flat` selects
smooth or flat surface shading independently. `--ortep-mode` independently
adds no marks, principal axes, or hatch marks.

Legacy `--config`, `--view-weights`, `--publication-*`, `--title`, and
`--subtitle` options are rejected by this agent-facing path instead of being
silently ignored. Polyhedron overlays remain available through repeatable
`--polyhedron` JSON objects plus `--polyhedron-site` and
`--polyhedron-cutoff`; they use the base CPU topology path and require no Web
module.


---

## `serve` — Browser viewer

Requires `python -m pip install "matter-vis[web]"`; this extra includes Plotly.

```
mat-vis serve [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Host to bind |
| `--port` | `50001` | Port to expose |
| `--cif` | — | CIF path to preload (repeat for multiple) |
| `--structure` | — | Limit catalog to named structure(s) |
| `--preset` | — | Preset JSON to load |
| `--api-only` | — | Reserved for automation mode |

## tui — Terminal view

Requires `python -m pip install "matter-vis[tui]"`. Use `mat-vis inspect --json`
for normal bounded agent diagnosis without installing Textual.

Open any supported structure or one trajectory frame without starting Dash:

~~~bash
mat-vis tui POSCAR
mat-vis tui trajectory.traj --frame 20
mat-vis tui run.dump --type-map O H --frame 20
~~~

Use --no-interaction for deterministic stdout. --format structured adds cell,
atom, bond-summary, and camera data. --input-format, --type-map, and --frame use
the same shared IO contract as render.

Important options:

- --display auto|unit_cell|formula_unit|asymmetric_unit selects a canonical scene;
- --view a|b|c|diagonal|ab|ac|bc or explicit angles selects the camera;
- --projection, --zoom, and --center work in static and interactive modes;
- --show-minor and --hide-partial control crystallographic disorder;
- --width and --height bound static output exactly.

Interactive controls: q/e and w/s orbit, a/d roll, arrows or i/j/k/l pan, u/o
zoom, b/c/t/m/n toggle bonds/cell/labels/monochrome/minor disorder, Shift+L
switches atom and molecule levels, r resets the view, and x quits. Press : for
selection, neighborhood focus, and geometric measurements.

## Common recipes

### Publication-quality ball-and-stick (JACS style)

```bash
mat-vis render mol.cif -o fig.png \
  --view formula_unit \
  --no-hydrogen --show-cell \
  --atom-scale 0.9 --bond-radius 0.14 \
  --background "#FFFFFF" \
  --width 1200 --height 900 --scale 3
```

### Full unit cell overview

```bash
mat-vis render crystal.cif -o cell.png \
  --backend cpu --view unit_cell \
  --no-hydrogen --show-cell \
  --atom-scale 0.7 --bond-radius 0.12
```

### ORTEP for crystallographic papers

```bash
mat-vis render crystal.cif -o ortep.pdf \
  --backend cpu --style ortep --material flat \
  --ortep-mode ortep_hatch --show-labels \
  --ortep-probability 0.5 --missing-adp-policy error
```

### Orthographic projection (no foreshortening)

```bash
mat-vis render crystal.cif -o ortho.png \
  --orthogonal --atom-scale 1.0 --camera-distance 2.5
```

### Interactive HTML for SI / supplementary info

```bash
mat-vis render crystal.cif -o si_figure.html \
  --backend plotly --view unit_cell --show-hydrogen --show-labels
```

---

## Troubleshooting

### Optional capability is not installed

Ask the resolver instead of guessing packages:

```bash
mat-vis capabilities --require plotly-export --json
mat-vis render structure.cif -o figure.png --backend plotly --check --json
```

Ordinary CPU PNG/PDF/SVG does not require Kaleido or Chrome.

### Large cell runs out of memory

For cells with >500 atoms, reduce supersampling:

```bash
mat-vis render big.cif -o fig.png --scale 1
```

If the scientific request is a local fragment, select it by a public molecule
or source identifier before rendering; `cluster` does not crop a periodic cell.

### Fonts not rendering correctly in PDF

Use a Unicode-capable font already available in the execution environment.
MatterVis does not install operating-system packages automatically.
