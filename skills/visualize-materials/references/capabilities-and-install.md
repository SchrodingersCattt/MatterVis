# Capabilities and Installation

Read this before installing anything or when `render --check` reports a missing
capability. The capability resolver is authoritative; do not maintain a second
dependency guess in an agent script.

## Preflight

```bash
mat-vis capabilities --json
mat-vis capabilities --require png ortep --json
mat-vis render INPUT.cif -o OUTPUT.svg --backend cpu --check --json
```

`--check` resolves requirements only. It does not load the structure or create
the output. Use the exact `install` command in its JSON result. Never install
`[all]` when a smaller reported extra is sufficient.

<!-- capability-matrix:start -->
| What you need | Resolver requirement | Required installation |
|---|---|---|
| CPU PNG/PDF/SVG; structure inspection; ORTEP; rings; polyhedra | `core` | `python -m pip install "matter-vis"` |
| Interactive Plotly/WebGL HTML | `plotly` | `python -m pip install "matter-vis[plotly]"` |
| Plotly PNG/PDF/SVG through Kaleido | `plotly-export` | `python -m pip install "matter-vis[plotly-export]"` |
| Dash viewer, REST, and WebSocket service | `web` | `python -m pip install "matter-vis[web]"` |
| Interactive terminal UI | `tui` | `python -m pip install "matter-vis[tui]"` |
| Cube input inspection and isosurfaces | `cube` | `python -m pip install "matter-vis[cube]"` |
| GIF/MP4 encoding | `animation` | `python -m pip install "matter-vis[animation]"` |
<!-- capability-matrix:end -->

Combined Web operations intentionally cross two optional boundaries:

| What you need | Resolver requirement | Required installation |
|---|---|---|
| REST/API browser screenshot | `web-screenshot` | `python -m pip install "matter-vis[plotly-export,web]"` |
| Web UI static Plotly export | `static-web-export` | `python -m pip install "matter-vis[plotly-export,web]"` |

These are requirement aliases, not new extras. They combine the Web service
with the Plotly/Kaleido encoder and never substitute a CPU image or placeholder
PNG when export fails.

Combine only the extras the request needs, for example:

```bash
python -m pip install "matter-vis[cube,animation]"
python -m pip install "matter-vis[web,tui]"
```

`python -m pip install "matter-vis[all]"` is reserved for development or a
caller who explicitly wants every frontend.

## Chrome and Kaleido

The CPU backend needs neither Chrome nor Kaleido. HTML needs Plotly but not
Chrome. Only Plotly static PNG/PDF/SVG needs `[plotly-export]`; Kaleido may also
need a working Chrome installation. MatterVis never downloads Chrome, runs
`apt`, or changes system libraries automatically. If an explicit Plotly static
render fails after the Python extra is installed, preserve the original error
and ask the caller to provide or authorize a compatible Chrome runtime. Do not
switch backend or representation silently.

## Development checkout

During the two-PR development window, MatterVis requires the exact MolCrysKit
contract commit used by CI. Install it first with:

```bash
python -m pip install "molcrys-kit @ git+https://github.com/SchrodingersCattt/MolCrysKit.git@f2188c14e245b87d99dc1d13ab72e37993d972b0"
```

Then install this checkout.
Released users should follow the resolver's ordinary `matter-vis[...]` command;
do not put a Git URL into generated runtime requirements.
