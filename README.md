# MatterVis: Crystal Structure Visualization Toolkit

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)](#)
[![CI](https://github.com/SchrodingersCattt/MatterVis/actions/workflows/ci.yml/badge.svg)](https://github.com/SchrodingersCattt/MatterVis/actions/workflows/ci.yml)

## Overview

MatterVis is a Python toolkit for interactive and publication-quality visualization of crystal structures. Built on Dash and Plotly, it provides a browser-based 3D viewer, coordination-topology analysis, ORTEP-style Matplotlib export, and a REST + WebSocket API for agent-driven automation.

## Key Features

- **Browser Viewer** — Drag-and-drop CIF upload, interactive 3D display with `Mesh3d` atoms and bonds, and a fast `Scatter3d` fallback for large cells
- **Coordination Topology** — Automatic coordination-number detection via the nearest-neighbour gap, continuous shape measure (CShM) classification against 12 ideal polyhedra (CN 4–12), planarity RMS, and prism/antiprism twist analysis
- **Publication Export** — Vendored ORTEP-style Matplotlib renderer with correct depth ordering, two-colour bonds, smart label placement, and configurable presets
- **Multi-Panel Figures** — `uniform_viewport(scenes)` stamps a shared world-cube on any list of scenes so every `build_figure` call emits at the same physical length per pixel
- **Automation API** — REST + WebSocket endpoints on the same Flask server; drive the viewer from notebooks, agents, or subprocesses
- **Zero Catalog Required** — Ships with a bundled DAP-4.cif so `mat-vis serve` works out of the box

<p align="center">
  <img src="docs/images/feature_unit_cell.png" width="500"><br>
  <em>Unit Cell Visualization — DAP-4 unit cell with 672 atoms</em>
</p>

<p align="center">
  <img src="docs/images/feature_coordination.png" width="500"><br>
  <em>Coordination Shell — A-site coordination with convex hull overlay</em>
</p>

<p align="center">
  <img src="docs/images/feature_three_modes.png" width="750"><br>
  <em>Three Display Modes — Formula unit, unit cell, and coordination shell side by side</em>
</p>

<p align="center">
  <img src="docs/images/feature_publication.png" width="500"><br>
  <em>Publication-Quality Export — ORTEP-style Matplotlib renderer</em>
</p>

## Installation

### From source (development)

```bash
git clone https://github.com/SchrodingersCattt/MatterVis.git
cd MatterVis
python -m pip install -r requirements.txt
python -m pip install -e .
```

All dependencies are declared in `pyproject.toml` and mirrored in `requirements.txt`.
`requires-python = ">=3.10"`. The available extras are:

| Extra | Adds |
|---|---|
| `[test]` | `pytest>=8.0`, `pytest-cov` |
| `[dev]` | `[test]` + `build`, `ruff` |

`molcrys_kit` is an optional runtime dependency. When available, the per-fragment
A/B/X heuristic uses its classifier; otherwise `crystal_viewer` uses built-in
element/size heuristics.

## Quick Start

Generate a publication-quality figure from a CIF file in a few lines:

```python
from crystal_viewer.loader import build_bundle_scene, build_loaded_crystal
from crystal_viewer.renderer import build_figure
from crystal_viewer.scene import scene_style

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

- `mat-vis render ...` — generate static figures (PNG/PDF/SVG/HTML) from CIF files
- `mat-vis serve ...` — launch the interactive Dash browser viewer
- `mat-vis tui ...` — terminal-based crystal structure viewer

## Documentation

| You are… | Start here |
|---|---|
| **Using the library** | [API Reference](docs/agents/) · [CLI Reference](docs/cli.md) |
| **AI agent (calling MatterVis)** | [Caller API Contracts](docs/agents/) |
| **AI agent (modifying code)** | [AGENTS.md](AGENTS.md) · [Developer Notes](docs/dev-notes.md) |
| **Topology scores** | [Scores Reference](docs/scores.md) |

[`crystal_viewer/`](crystal_viewer/) — source code · [`scripts/`](scripts/) — runnable demo scripts · [`docs/`](docs/) — full documentation

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
