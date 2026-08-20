# CLI Reference

MatterVis renders atomistic structures and trajectories without launching the
browser viewer. Every input format is parsed into the same canonical structure
frame before scene construction.

## Quick start

~~~bash
# Static CIF
mat-vis render structure.cif -o figure.png

# VASP structure
mat-vis render POSCAR -o figure.png --view unit_cell

# One frame from an ASE trajectory
mat-vis render trajectory.traj --frame 20 -o frame.png

# LAMMPS trajectory with explicit atom-type order
mat-vis render run.dump --type-map O H -o trajectory.gif \
  --frame-range 0:100:2 --fps 12

# Interactive HTML
mat-vis render structure.extxyz -o interactive.html --orthogonal
~~~

## Subcommands

| Command | Description |
|---------|-------------|
| render | Render a structure, one trajectory frame, or a GIF/MP4 animation |
| serve | Launch the interactive Dash browser viewer |
| tui | Open a supported structure or trajectory frame in the terminal |

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
| CIF | High-fidelity Gemmi and MolCrysKit path |
| Cube | Cube parser with volumetric data preserved |
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
| .png | Raster image | Plotly + Kaleido; Matplotlib fallback |
| .pdf | Vector PDF | Plotly + Kaleido; Matplotlib fallback |
| .svg | Vector SVG | Plotly + Kaleido; Matplotlib fallback |
| .html | Interactive 3D | Plotly.js |
| .gif | Multi-frame animation | Shared renderer + Pillow |
| .mp4 | H.264 animation | Shared renderer + imageio-ffmpeg |

### Frames and animations

| Flag | Default | Description |
|------|---------|-------------|
| --frame INDEX | 0 | Select one frame for PNG/PDF/SVG/HTML |
| --frame-range START:STOP[:STEP] | all | Python half-open frame slice for GIF/MP4 |
| --stride N | 1 | Keep every Nth selected animation frame |
| --fps FPS | 12 | Positive animation frame rate |

GIF/MP4 require at least two selected frames. All selected frames use one camera,
canvas, and shared world-space viewport scale.

Animations preserve one requested visual language across every frame. If a
Plotly-backed animation style cannot be exported because Chrome/Kaleido is
unavailable or fails, the command stops instead of silently substituting flat
ORTEP frames. Browser-free animation remains available when explicitly requested
with `--material flat --style ortep`.

### Display options

| Flag | Default | Description |
|------|---------|-------------|
| --view MODE | auto | CIF defaults to formula_unit; other inputs to unit_cell |
| --style STYLE | ball_stick | ball_stick, ball, stick, ortep, or wireframe |
| --material MAT | mesh | mesh or flat |
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
| `--camera-position X Y Z` | — | Explicit Plotly eye position relative to scene centre |
| `--camera-up X Y Z` | `+b` / `+Y` | Preferred screen-up direction |
| `--camera-distance D` | `1.8` | Positive eye distance for axis/direction views |

`--camera-up` is orthogonalized against the view direction. Static Plotly
PNG/PDF/SVG export is attempted first. If local Chrome/Kaleido export is
unavailable, the CLI reports the error and falls back to the browser-free
Matplotlib `material=flat, style=ortep` path; that fallback is ORTEP rather
than an exact replacement for a requested mesh or ball-and-stick style. This
style substitution is static-only and is never applied frame-by-frame to an
animation.

### Visibility toggles

| Flag | Default | Description |
|------|---------|-------------|
| `--show-hydrogen` / `--no-hydrogen` | off | Show/hide hydrogen atoms |
| `--show-cell` / `--no-cell` | on | Show/hide unit cell edges |
| `--show-axes` / `--no-axes` | on | Show/hide lattice axes |
| `--show-labels` / `--no-labels` | off | Show/hide atom labels |
| `--monochrome` | off | Render in greyscale |

### Numeric parameters

| Flag | Default | Range | Description |
|------|---------|-------|-------------|
| `--atom-scale` | 1.0 | 0.3–1.8 | Atom radius scale factor |
| `--bond-radius` | 0.15 | 0.05–0.40 | Bond cylinder radius (Å) |
| `--camera-distance` | 1.8 | > 0 | Camera eye distance |
| `--width` | 900 | — | Image width in pixels |
| `--height` | 720 | — | Image height in pixels |
| `--scale` | 2 | 1–4 | Supersampling factor (effective DPI = 72 × scale) |

### Colour and ORTEP

| Flag | Default | Description |
|------|---------|-------------|
| `--background` | `#FFFFFF` | Background hex colour |
| `--ortep-probability` | 0.5 | Ellipsoid probability (0.0–1.0) |
| `--ortep-mode` | `ortep_axes` | ORTEP variant: `ortep_solid`, `ortep_axes`, `ortep_octant`, `ortep_hatch` |

### Advanced: full style override via JSON

```bash
mat-vis render structure.cif -o fig.png --config style.json
```

The `--config` flag loads a JSON file containing any style keys from the
full MatterVis style schema. CLI flags take precedence over config values.

Example `style.json`:

```json
{
  "disorder": "opacity",
  "minor_opacity": 0.25,
  "force_bond_color": "#1A1A1A",
  "element_colors": {
    "N": "#3366CC",
    "Cl": "#33AA33"
  },
  "depth_cue_enabled": true
}
```

This covers advanced parameters not exposed as CLI flags: disorder modes,
element colour overrides, force bond colour, depth cue, axis key overlay
settings, and ORTEP fine-tuning (silhouette, hatch linewidths, z-lifts).

### Dense coordination publication layout

`--publication-layout` requires at least one `--polyhedron` specification. For
PNG/PDF/SVG it uses the deterministic Matplotlib compositor, so it does not
require Kaleido and preserves the requested canvas dimensions. HTML continues
to use the interactive Plotly compositor.

The `dense_coordination` preset owns material, transparency, sphere lighting,
front/back ligand layers, and normalized layout. It does not prescribe a
camera. Keep any structure-specific camera choice outside the reusable
material preset. See [the static publication contract](agents/static_publication.md)
for the parameter table and acceptance checks.


---

## `serve` — Browser viewer

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
  --no-hydrogen --no-axes --show-cell \
  --atom-scale 0.9 --bond-radius 0.14 \
  --background "#FFFFFF" \
  --width 1200 --height 900 --scale 3
```

### Full unit cell overview

```bash
mat-vis render crystal.cif -o cell.png \
  --view unit_cell \
  --no-hydrogen --show-cell --show-axes \
  --atom-scale 0.7 --bond-radius 0.12
```

### ORTEP for crystallographic papers

```bash
mat-vis render crystal.cif -o ortep.pdf \
  --style ortep --ortep-mode ortep_hatch \
  --monochrome --no-axes --show-labels \
  --ortep-probability 0.5
```

### Orthographic projection (no foreshortening)

```bash
mat-vis render crystal.cif -o ortho.png \
  --orthogonal --atom-scale 1.0 --camera-distance 2.5
```

### Interactive HTML for SI / supplementary info

```bash
mat-vis render crystal.cif -o si_figure.html \
  --view unit_cell --show-hydrogen --show-labels
```

---

## Troubleshooting

### `kaleido` not installed

PNG/PDF/SVG export requires the `kaleido` package:

```bash
pip install kaleido
```

### Large cell runs out of memory

For cells with >500 atoms, reduce supersampling:

```bash
mat-vis render big.cif -o fig.png --scale 1
```

Or use the `cluster` display mode to show only a molecular fragment.

### Fonts not rendering correctly in PDF

Ensure a Unicode-capable font is available on the system. On headless
Linux servers:

```bash
apt-get install fonts-dejavu-core
```
