# MatterVis: Crystal Structure Visualization Toolkit

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](#)
[![PyPI](https://img.shields.io/pypi/v/matter-vis.svg)](https://pypi.org/project/matter-vis/)
[![CI](https://github.com/SchrodingersCattt/MatterVis/actions/workflows/release.yml/badge.svg)](https://github.com/SchrodingersCattt/MatterVis/actions/workflows/release.yml)

## Overview

MatterVis is a Python toolkit for interactive and publication-quality visualization of crystal structures. Built on Dash and Plotly, it provides a browser-based 3D viewer, coordination-topology analysis, ORTEP-style Matplotlib export, and a REST + WebSocket API for agent-driven automation.

## Key Features

- **Headless CLI** — Three subcommands (`render`, `serve`, `tui`) cover the full workflow: CIF → publication-quality figure, interactive 3D viewer, or terminal-based inspection. Ideal for batch processing, CI pipelines, and remote servers
- **Browser Viewer** — Drag-and-drop CIF upload, interactive 3D display with `Mesh3d` atoms and bonds, and a fast `Scatter3d` fallback for large cells. The viewer uses the built-in element palette by default
- **Coordination Topology** — Automatic coordination-number detection via the nearest-neighbour gap, continuous shape measure (CShM) classification against 12 ideal polyhedra (CN 4–12), planarity RMS, and prism/antiprism twist analysis
- **Publication Export** — Vendored ORTEP-style Matplotlib renderer with correct depth ordering, two-colour bonds, smart label placement, and configurable presets
- **Multi-Panel Figures** — `uniform_viewport(scenes)` stamps a shared world-cube on any list of scenes so every `build_figure` call emits at the same physical length per pixel
- **Automation API** — REST + WebSocket endpoints on the same Flask server; drive the viewer from notebooks, agents, or subprocesses
- **Zero Catalog Required** — Ships with a bundled DAP-4.cif so `mat-vis serve` works out of the box

<p align="center">
  <img src="docs/images/feature_unit_cell.png" width="500"><br>
  <em>Unit Cell — DAP-4 unit cell (flat ORTEP with element colours)</em>
</p>

<p align="center">
  <img src="docs/images/feature_coordination.png" width="500"><br>
  <em>Coordination Shell — A-site coordination with convex hull overlay (CN=9, tricapped trigonal prism)</em>
</p>

<p align="center">
  <img src="docs/images/feature_three_modes.png" width="750"><br>
  <em>Three Display Modes — Formula unit, unit cell, and coordination shell side by side</em>
</p>

<p align="center">
  <img src="docs/images/feature_publication.png" width="500"><br>
  <em>Publication-Quality Export — Colour ORTEP-style Matplotlib renderer with labels</em>
</p>

<p align="center">
  <img src="docs/images/feature_asymmetric_unit.png" width="500"><br>
  <em>Asymmetric Unit — Diagnostic crystallographic view with atom labels and unit-cell context</em>
</p>

## Installation

### From PyPI (recommended)

```bash
pip install matter-vis
```

### From source (development)

```bash
git clone https://github.com/SchrodingersCattt/MatterVis.git
cd MatterVis
pip install -e .
```

All dependencies are declared in `pyproject.toml`.
`requires-python = ">=3.10"`. The available extras are:

| Extra | Adds |
|---|---|
| `[test]` | `pytest>=8.0`, `pytest-cov` |
| `[dev]` | `[test]` + `build`, `ruff` |

`molcrys_kit` is an optional runtime dependency. When available, the per-fragment
A/B/X heuristic uses its classifier; otherwise `mat_viewer` uses built-in
element/size heuristics.

## Quick Start

### CLI — one-liner from CIF to figure

Install MatterVis, then render a crystal structure with a single command:

```bash
# PNG with default ball-and-stick style
mat-vis render structure.cif -o figure.png

# PDF, full unit cell, ORTEP hatch shading in greyscale
mat-vis render structure.cif -o figure.pdf \
  --view unit_cell --style ortep --ortep-mode ortep_hatch --monochrome

# Interactive HTML for supplementary information
mat-vis render structure.cif -o si_figure.html \
  --show-hydrogen --show-labels

# Launch the interactive browser viewer
mat-vis serve --cif structure.cif
```

See [`docs/cli.md`](docs/cli.md) for the full flag reference and common recipes.

### Python API — programmatic control

```python
from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from mat_viewer.renderer import build_figure
from mat_viewer.scene import scene_style

bundle = build_loaded_crystal(name="DAP-4", cif_path="scripts/data/DAP-4.cif")
scene  = build_bundle_scene(bundle, display_mode="unit_cell")
style  = scene_style(scene, {"show_unit_cell": True})

fig = build_figure(scene, style)
fig.write_image("dap4.png", width=900, height=720, scale=2)
fig.write_html("dap4.html", include_plotlyjs="cdn")
```

## Command Line Interface

Installing MatterVis also installs the `mat-vis` command. The CLI is self-documenting;
use `--help` at any level to see the exact arguments:

```bash
mat-vis --help
mat-vis render --help
mat-vis serve --help
mat-vis tui --help
```

The three subcommands cover:

- `mat-vis render ...` — render atomistic structures and trajectories from CIF, Cube, VASP, XYZ, ASE, and LAMMPS inputs to PNG/PDF/SVG/HTML/GIF/MP4
- `mat-vis serve ...` — launch the interactive Dash browser viewer with drag-and-drop CIF upload, topology analysis, and REST + WebSocket API
- `mat-vis tui ...` — terminal-based crystal structure viewer for headless servers and SSH sessions

## Documentation

| You are… | Start here |
|---|---|
| **Using the library** | [API Reference](docs/agents/) · [CLI Reference](docs/cli.md) |
| **AI agent (calling MatterVis)** | [Caller API Contracts](docs/agents/) |
| **AI agent (modifying code)** | [AGENTS.md](AGENTS.md) · [Developer Notes](docs/dev-notes.md) |
| **Topology scores** | [Scores Reference](docs/scores.md) |

[`mat_viewer/`](mat_viewer/) — source code · [`scripts/`](scripts/) — runnable demo scripts · [`docs/`](docs/) — full documentation

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
