# CLI Reference

MatterVis provides a command-line interface for generating publication-quality
crystal structure figures without launching the browser viewer.

## Quick start

```bash
# Render a CIF to PNG (publication quality, 2× supersampling)
python -m crystal_viewer render structure.cif -o figure.png

# PDF output, unit cell view, no hydrogen
python -m crystal_viewer render structure.cif -o figure.pdf \
  --view unit_cell --no-hydrogen

# ORTEP with hatch shading in greyscale
python -m crystal_viewer render structure.cif -o ortep.png \
  --style ortep --ortep-mode ortep_hatch --monochrome

# Interactive HTML with orthographic projection
python -m crystal_viewer render structure.cif -o interactive.html \
  --orthogonal --atom-scale 1.2
```

## Subcommands

| Command | Description |
|---------|-------------|
| `render` | Generate a static figure from a CIF file |
| `serve`  | Launch the interactive Dash browser viewer |

---

## `render` — Static figure export

```
python -m crystal_viewer render CIF -o OUTPUT [options]
```

### Required arguments

| Argument | Description |
|----------|-------------|
| `CIF` | Path to the input CIF file |
| `-o`, `--output` | Output file path. Format inferred from extension |

### Supported output formats

| Extension | Format | Backend |
|-----------|--------|---------|
| `.png` | Raster image | Plotly + kaleido |
| `.pdf` | Vector PDF | Plotly + kaleido |
| `.svg` | Vector SVG | Plotly + kaleido |
| `.html` | Interactive 3D | Plotly.js (CDN) |

### Display options

| Flag | Default | Description |
|------|---------|-------------|
| `--view MODE` | `formula_unit` | Display mode: `formula_unit`, `unit_cell`, `asymmetric_unit`, `cluster` |
| `--style STYLE` | `ball_stick` | Rendering style: `ball_stick`, `ball`, `stick`, `ortep`, `wireframe` |
| `--material MAT` | `mesh` | Surface material: `mesh`, `flat` |
| `--orthogonal` | — | Use orthographic projection |
| `--perspective` | ✓ | Use perspective projection (default) |

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
| `--camera-distance` | 1.8 | 0.5–5.0 | Camera eye distance |
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
python -m crystal_viewer render structure.cif -o fig.png --config style.json
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

---

## `serve` — Browser viewer

```
python -m crystal_viewer serve [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Host to bind |
| `--port` | `50001` | Port to expose |
| `--cif` | — | CIF path to preload (repeat for multiple) |
| `--structure` | — | Limit catalog to named structure(s) |
| `--preset` | — | Preset JSON to load |
| `--api-only` | — | Reserved for automation mode |

---

## Common recipes

### Publication-quality ball-and-stick (JACS style)

```bash
python -m crystal_viewer render mol.cif -o fig.png \
  --view formula_unit \
  --no-hydrogen --no-axes --show-cell \
  --atom-scale 0.9 --bond-radius 0.14 \
  --background "#FFFFFF" \
  --width 1200 --height 900 --scale 3
```

### Full unit cell overview

```bash
python -m crystal_viewer render crystal.cif -o cell.png \
  --view unit_cell \
  --no-hydrogen --show-cell --show-axes \
  --atom-scale 0.7 --bond-radius 0.12
```

### ORTEP for crystallographic papers

```bash
python -m crystal_viewer render crystal.cif -o ortep.pdf \
  --style ortep --ortep-mode ortep_hatch \
  --monochrome --no-axes --show-labels \
  --ortep-probability 0.5
```

### Orthographic projection (no foreshortening)

```bash
python -m crystal_viewer render crystal.cif -o ortho.png \
  --orthogonal --atom-scale 1.0 --camera-distance 2.5
```

### Interactive HTML for SI / supplementary info

```bash
python -m crystal_viewer render crystal.cif -o si_figure.html \
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
python -m crystal_viewer render big.cif -o fig.png --scale 1
```

Or use the `cluster` display mode to show only a molecular fragment.

### Fonts not rendering correctly in PDF

Ensure a Unicode-capable font is available on the system. On headless
Linux servers:

```bash
apt-get install fonts-dejavu-core
```
