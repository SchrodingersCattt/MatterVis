# Showcase Images

Generates the 6 README showcase images from `scripts/data/DAP-4.cif`:

| Function | Output | Description |
|---|---|---|
| `render_unit_cell` | `feature_unit_cell.png` | DAP-4 unit cell (Plotly, 900×720) |
| `render_coordination` | `feature_coordination.png` | A-site coordination shell (Plotly, 900×720) |
| `render_coordination` | `feature_histogram.png` | Distance histogram (Plotly, 900×380) |
| `render_three_modes` | `feature_three_modes.png` | 3-panel: formula unit / unit cell / coordination shell (Plotly, 1500×560) |
| `render_publication` | `feature_publication.png` | Matplotlib flat ORTEP (240 dpi) |
| `render_banner` | `banner.png` | Combined banner from cell + coordination + publication |

## Usage

```bash
# From repo root
python paper/showcase/build_images.py
```

Output lands in `docs/images/` (committed; no build step required for README rendering).