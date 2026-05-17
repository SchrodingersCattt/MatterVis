# MatterVis · crystal_viewer

Standalone Dash / Plotly frontend and automation toolkit for molecular
perchlorate crystals (**A**₂**B**(ClO₄)₄ and friends). CIF in — interactive 3D
viewer, coordination-topology scores, publication-quality Matplotlib export
and a REST + WebSocket API for other agents, all out.

![banner](docs/images/banner.png)

Everything in this README is reproduced from the bundled
`scripts/data/DAP-4.cif` (a triclinic diammonium diperchlorate `P1` cell,
see [`scripts/data/README.md`](scripts/data/README.md)); swap it for your
own CIF with a single flag.

---

## Highlights

- **Browser viewer** — Dash front-end, drag-and-drop CIF upload, formula /
  unit-cell / asymmetric-unit / isolated-cluster display, `Mesh3d` atoms and
  bonds with a fast `Scatter3d` fallback for large cells.
- **Coordination topology** — automatic CN detection via the nearest-neighbour
  gap, angular RMSD vs 12 ideal polyhedra (CN 8-12), planarity RMS of any
  5-atom subset and a prism / antiprism twist check. See
  [`docs/scores.md`](docs/scores.md).
- **Publication export** — vendored ORTEP-style Matplotlib renderer with
  correct depth ordering, two-colour bonds, smart label placement and
  configurable presets. Plotly path shares radius-aware viewport bounds so
  large halides never clip at the panel edge.
- **Multi-panel figures** — `uniform_viewport(scenes)` stamps a shared
  world-cube on any list of scenes so every `build_figure` call emits at the
  same physical length per pixel. Drop-in for N-up comparison figures.
- **Automation** — REST + WebSocket API on the same Flask server. Drive the
  viewer from notebooks, agents or subprocesses (`GET /api/v1/state`,
  `POST /api/v1/topology`, `GET /api/v1/screenshot`, ...).
- **Zero catalog required** — the package ships with a single public CIF so
  `python -m crystal_viewer --cif scripts/data/DAP-4.cif` just works.

## Install

```bash
git clone https://github.com/SchrodingersCattt/MatterVis.git
cd MatterVis
python -m pip install -r requirements.txt
```

`molcrys_kit` is optional. When available the per-fragment **A / B / X**
heuristic falls back to its classifier; otherwise `crystal_viewer` uses its
built-in element / size heuristics. The browser UI doesn't expose A/B/X
labels directly -- it lists the stoichiometric formulas it detects (e.g.
`C6N2 ×2`, `ClO4 ×4`, `N ×1`) so the same controls work for non-perovskite
crystals.

## Launch the browser viewer

```bash
python -m crystal_viewer --cif scripts/data/DAP-4.cif
# Serving crystal viewer at http://127.0.0.1:8051
```

Additional flags:

```bash
python -m crystal_viewer --port 8051            # pick a port
python -m crystal_viewer --host 0.0.0.0         # bind on all interfaces
python -m crystal_viewer --structure DAP-4      # limit catalog to one name
python -m crystal_viewer --cif a.cif --cif b.cif --cif c.cif
```

`--cif` is `action="append"` -- repeat the flag once per CIF file you want to
preload. The viewer listens on `127.0.0.1` by default; pass `--host 0.0.0.0`
when the dev box is behind a port-forwarding proxy (see below).

### Remote dev boxes (Bohrium and friends)

Container platforms typically only expose a small range of ports. On a
**Bohrium tech development machine** the public proxy maps the URL
`http://<your-id>.bohrium.tech:5000X` straight through to container port
`5000X` for `X` in `1..5` -- nothing else is reachable from outside. Two
common gotchas:

1. **Bind to `0.0.0.0`.** Loopback-only listeners (the `--host 127.0.0.1`
   default) never see traffic from the platform proxy.
2. **Use one of `50001`-`50005`.** Other ports on the container may be in
   use by Jupyter / MCP servers; check with `ss -ltn | grep 5000` before
   you pick one.

Putting it together for a Bohrium dev box:

```bash
python -m crystal_viewer.app --host 0.0.0.0 --port 50001 \
  --cif scripts/data/DAP-4.cif --cif scripts/data/SY.cif
# Reachable at http://<your-id>.bohrium.tech:50001/
```

If the URL still ERR_CONNECTION_REFUSED, port `50001` is already taken on
that container -- pick another from the `50001-50005` window.

### Open the page in a real browser, not an embedded webview

Cursor's *Simple Browser*, VS Code's *preview panel* and similar
Electron-hosted webviews dispatch click events at the DOM level but
do not always propagate them through React 16's synthetic event
system that Dash binds onto. The visible failure mode is brutal:
**every checkbox / dropdown / slider appears to "click" but nothing
happens** -- the figure never updates, the network tab is silent
(no `/_dash-update-component` POST is ever sent), and the user
concludes the app is broken.

The viewer auto-detects this case by sniffing the
`User-Agent` and renders a sticky red banner at the top of the page
with a one-click *Copy URL* button. Paste the URL into Chrome, Edge,
Firefox or Safari and every control will start firing as expected.

A built-in wire-tap strip is also available behind the
`?diag=1` query parameter -- it shows live counts of clicks, Dash
POSTs and JS errors so you can tell at a glance whether a "no
response" report is a DOM problem (no clicks), an event-delegation
problem (clicks but no POST), or a server problem (POST but no OK).

For server-side debugging the same information is available via
`MATTERVIS_AUDIT=1 python -m crystal_viewer.app ...`, which prints a
one-line summary of every non-poll callback (changedPropIds,
duration, payload size, originating IP / User-Agent).

See [`AGENTS.md`](AGENTS.md) for every REST / WebSocket endpoint and the full
set of stable UI element IDs.

## Headless scripts

Every script in [`scripts/`](scripts) can be run end-to-end without a
browser:

```bash
python scripts/01_quick_render.py           # CIF -> PNG + interactive HTML
python scripts/02_coordination_analysis.py  # coordination shell + all scores
python scripts/03_display_modes_panel.py    # formula / unit cell / shell
python scripts/04_static_publication.py     # ORTEP-style PNG + PDF
python scripts/05_app_and_api.py            # launch app + drive it via REST
python scripts/06_cp2k_cube_orbital.py --cube orbital.cube  # CP2K/Gaussian cube isosurfaces
```

Outputs land under `scripts/_outputs/` (gitignored). Regenerate the README
showcase images with `python docs/build_images.py`.

### CP2K / Gaussian cube orbitals

MatterVis can render CP2K or Gaussian `.cube` orbital files as paired positive
and negative Plotly isosurfaces, with atom positions overlaid from the cube
header:

```bash
python scripts/06_cp2k_cube_orbital.py \
  --cube /path/to/cp2k-WFN_00292_1-1_0.cube \
  --output-prefix HOCO \
  --stride 2 \
  --percentile 98.5
```

The reader converts cube coordinates from Bohr to Å and automatically chooses a
robust isovalue from the absolute-value distribution unless `--isovalue` is
provided. HTML output is always generated; PNG export is attempted when Kaleido
is available.

### 1. `01_quick_render.py` — CIF to unit cell in ten lines

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

![unit cell](docs/images/feature_unit_cell.png)

### 2. `02_coordination_analysis.py` — topology scores with one function call

```python
from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.topology import analyze_topology

bundle = build_loaded_crystal(name="DAP-4", cif_path="scripts/data/DAP-4.cif")
# Pick the first DABCO ring -- in DAP-4 those have stoichiometry C6N2.
target = next(f for f in bundle.topology_fragment_table if f.get("formula") == "C6N2")
result = analyze_topology(bundle, center_index=target["index"], cutoff=8.0)

print(result["coordination_number"])             # 9
print(result["shape"]["primary_label"])          # tricapped_trigonal_prism
print(result["shape"]["label_modifier"])         # distorted
print(result["gap_info"]["gap_value"])           # 0.124 Å
```

The example also dumps a tidy `02_coordination_summary.json` with every score
broken out — see [`docs/scores.md`](docs/scores.md) for a full reference.

<p>
  <img src="docs/images/feature_coordination.png" alt="coordination hull" width="49%"/>
  <img src="docs/images/feature_histogram.png"    alt="distance histogram" width="49%"/>
</p>

### 3. `03_display_modes_panel.py` — side-by-side display modes

A single Plotly figure stitches formula unit, unit cell, and coordination
shell together so reviewers can switch between them without reloading:

![three modes](docs/images/feature_three_modes.png)

### 4. `04_static_publication.py` — ORTEP-style Matplotlib export

Runs the vendored `crystal_viewer.static_publication.plot_crystal` renderer (same code
used by `POST /api/v1/export`) to produce high-DPI PNG + PDF suitable for
Nature-style figures:

![publication](docs/images/feature_publication.png)

### 5. `05_app_and_api.py` — drive the live viewer over HTTP

Starts `create_app()` in the background, preloads `DAP-4`, then hits
`POST /api/v1/topology` and `GET /api/v1/screenshot`. The call sequence is a
one-page recipe for wiring the viewer into a larger agent pipeline.

## Topology scores

`analyze_topology()` (and the REST `POST /api/v1/topology`) return five
named scores that together describe a coordination shell. One line summary:

| Score | Module field | What it measures |
| --- | --- | --- |
| Coordination number | `coordination_number` + `gap_info.gap_value` | Neighbours in the first shell, picked by the largest distance jump. |
| Shape classification | `shape.primary_label` / `label_modifier` / `cshm_value` | CShM-based polyhedron name + distortion tier (`clean` / `distorted` / `ambiguous`) for CN 4-12. |
| Planarity RMS | `planarity.best_rms` / `best_indices` | Best-fit plane through any 5 shell atoms (Å). |
| Prism / antiprism twist | `prism_analysis.twist_deg` / `classification` | Average inter-ring rotation; threshold 18°, only for CN ≥ 10. |
| Convex hull | `hull.vertices` / `simplices` / `edges` | Geometry the viewer draws as the purple polyhedron. |

See [`docs/scores.md`](docs/scores.md) for how each score is computed,
reasonable thresholds, and worked numbers for DAP-4.

## Rendering isolated clusters

Perchlorate-cluster CIFs (dummy 100 Å cell, atoms listed in P1, no periodic
imaging expected) go through a dedicated display mode that skips formula-unit
trimming and MIC bond search:

```python
from crystal_viewer.scene import build_scene_from_cif
from crystal_viewer.renderer import build_figure, uniform_viewport
from crystal_viewer.presets import DEFAULT_STYLE, deep_merge

# Palette override: add elements not in the vendored table (I, Na, K, Rb, …)
# or restyle existing ones for a specific figure.
style = deep_merge(DEFAULT_STYLE, {
    "show_title": False,
    "show_labels": False,
    "show_axes": False,
    "show_hydrogen": True,
    "atom_scale": 0.9,
    "bond_radius": 0.14,
    "element_colors": {"I": "#940094", "Na": "#E6D11E", "K": "#AB82FF"},
})
preset = {"version": 1, "style": style, "structures": {}}

scenes = [
    build_scene_from_cif(
        name=name,
        cif_path=f"clusters/{name}.cif",
        title=name,
        preset=preset,
        show_hydrogen=True,
        display_mode="cluster",
    )
    for name in ("DAI-1", "DAI-4", "DAP-2", "DAP-M4")
]

# Pin every panel to the same world cube so panel-to-panel scale is identical
# and no Cl / I atoms clip at the edge. The cube side = biggest scene span +
# padding (in Å).
uniform_viewport(scenes, padding=0.5)

for scene in scenes:
    fig = build_figure(scene, style)
    fig.write_image(f"{scene['name']}.png", width=600, height=600, scale=2)
```

Every scene now renders at the same Å-per-pixel, with radius-aware bounds so
even Cl / I / Br spheres are fully visible. `display_mode="cluster"` honours
the stored Cartesian coordinates exactly — useful when the CIF encodes a
hand-curated fragment rather than a crystallographic asymmetric unit.

## Package layout

```
crystal_viewer/
├── __init__.py          # re-exports create_app
├── __main__.py          # `python -m crystal_viewer` entry point
├── app/                 # Dash layout, callbacks, ViewerBackend internals
├── api/                 # REST + WebSocket blueprints
├── render/              # Plotly viewport / traces / cache internals
├── structure/           # CIF parsing, bonds, formula units, MolCrysKit bridge
├── loader/              # CIF/fragment bundle loading facade + upload helpers
├── scene/               # cell/cluster scene builder facade
├── transforms/          # repeat/grow/slab transform primitives and pipeline
├── topology/            # coordination-shell extraction & shape analysis
├── style/               # atom/bond rules, palette, disorder render helpers
├── cube/                # cube/orbital I/O, meshes, traces, export facade
├── ortep/               # thermal ellipsoid math, billboards, traces
├── compass/             # camera-projected lattice direction annotations
├── renderer/            # public Plotly facade for atoms, bonds, hulls, axes
├── presets/             # preset / style / catalog IO
├── scenes/              # tab/session scene state
├── perf_log/            # lightweight server event log facade
├── depth_sort/          # matplotlib depth-order helpers
├── viewer_backend/      # public ViewerBackend compatibility facade
├── assets/              # Dash CSS + JS for the panel layout
└── static_publication/  # matplotlib publication exporter
scripts/                # runnable demo scripts (see section above)
docs/                    # README showcase images + scores.md
```

## Notes

- Plotly screenshot export uses `kaleido`; the first call is slow because
  it spins up a headless Chromium.
- Static publication export writes to `.exports/` (gitignored).
- Local presets default to `.local/crystal_view_preset.json`.
- Local catalog overrides can be supplied via `catalog.local.json` or
  `.local/catalog.local.json`.
- The CIF parser handles Materials-Studio style loops that omit
  `_atom_site_disorder_*` columns — no manual cleanup required.
- For automation details (every endpoint, stable UI IDs, WebSocket schema),
  see [`AGENTS.md`](AGENTS.md).

## Citing

If you use the bundled example structure, please also credit the originating
publication that released it — see [`scripts/data/README.md`](scripts/data/README.md).
The `crystal_viewer` code itself is released under the repository's root
license file.
