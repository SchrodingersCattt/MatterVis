# Calling MatterVis

This folder is for agents and humans **calling** MatterVis — either via
its HTTP/WebSocket service or by importing `mat_viewer` from Python.
If you are **modifying** the codebase itself, read [`../../AGENTS.md`](../../AGENTS.md)
instead.

## Where to drop in

Start from the caller intent and follow the arrow to the doc that owns
the matching public surface.

```mermaid
flowchart LR
    Q["What do you want to do?"]
    Q --> LIVE["Drive the running viewer"]
    Q --> TUI["Control a terminal view"]
    Q --> STATIC["Render a structure or trajectory"]
    Q --> OVERLAY["Tweak overlays or selectors"]

    LIVE --> DASH["dash_service.md<br/>(REST + WebSocket)"]
    TUI --> TUIAPI["tui_api.md<br/>(local semantic controller)"]

    STATIC --> CLI["../cli.md<br/>(files, frames, animation)"]
    STATIC --> SCENE["scene_api.md<br/>(canonical scene API)"]
    STATIC --> CUBE["cube_api.md<br/>(orbitals / volumetric)"]
    STATIC --> ORTEP["ortep_api.md<br/>(thermal ellipsoids)"]
    STATIC --> COMPASS["compass_api.md<br/>(axis triads, k-paths)"]
    STATIC --> PROPERTY["atom_property_coloring.md<br/>(charge / velocity / stress)"]

    OVERLAY --> POLY["polyhedron_api.md"]
    OVERLAY --> AG["atom_groups_api.md"]
    OVERLAY --> BG["bond_groups_api.md"]
    OVERLAY --> TR["transforms_api.md"]
    OVERLAY --> SEL["selection_api.md"]
    OVERLAY --> VEC["vector_overlays_api.md<br/>(anchored world vectors)"]
    LIVE --> CONFIG["config_api.md"]
```

The same routing as a quick table:

| If you want to… | Read |
|---|---|
| Drive the running Dash viewer over HTTP/WebSocket | [`dash_service.md`](dash_service.md) |
| Control a local terminal view, select stable atom IDs, or run a stateful Python/JSONL agent session | [`tui_api.md`](tui_api.md) |
| Render CIF, Cube, VASP, XYZ, ASE trajectory, or LAMMPS files from the CLI | [`../cli.md`](../cli.md) |
| Label trajectory frames with physical simulation time while keeping playback FPS independent | [`../cli.md`](../cli.md#frames-and-animations) |
| Build or compose a canonical structure scene from Python | [`scene_api.md`](scene_api.md) |
| Match a dense coordination-polyhedron publication layout | [`static_publication.md`](static_publication.md) |
| Build a static cube/orbital figure (HOMO, LUMO, density) | [`cube_api.md`](cube_api.md) |
| Render ORTEP / thermal ellipsoid figures | [`ortep_api.md`](ortep_api.md) |
| Color atoms by charge, velocity, stress, or another per-atom field | [`atom_property_coloring.md`](atom_property_coloring.md) |
| Add a/b/c (or x/y/z, k-path, dipole) direction indicators to any 3D plot | [`compass_api.md`](compass_api.md) |
| Manage named coordination polyhedra (per-row colour, ligand restriction, per-instance overrides) | [`polyhedron_api.md`](polyhedron_api.md) |
| Apply per-element / per-group colour, visibility, material, or render-style overrides from API or CLI | [`atom_groups_api.md`](atom_groups_api.md) |
| Draw anchored polarization, dipole, force, displacement, magnetic, or response vectors with real 3D depth | [`vector_overlays_api.md`](vector_overlays_api.md) |
| Recolour, hide, restyle, thin out, or fade chemical bonds by selector from API or CLI | [`bond_groups_api.md`](bond_groups_api.md) |
| Repeat a unit cell, grow by radius / bonds, complete fragments / polyhedra, or generate a slab | [`transforms_api.md`](transforms_api.md) |
| Audit which backend-neutral render controls are available from the CLI | [`cli-api-parity.md`](cli-api-parity.md) |
| Read or override global MatterVis defaults | [`config_api.md`](config_api.md) |
| Select atoms/fragments in the live viewer and promote a selection to a group | [`selection_api.md`](selection_api.md) |

## Repo ownership for agents

When you are modifying MatterVis rather than just calling it, keep the
top-level ownership boundaries clear:

| Directory | Owns |
|---|---|
| `mat_viewer/` | Python library code (`structure`, `ops`, `analysis`, `render`, `scene`, `math`, `utils`) |
| `api/` | REST/WebSocket route modules for scripts and agents |
| `app/` | Dash Python BFF: layouts, callbacks, and `ViewerBackend` |
| `frontend/` | Browser-only JavaScript and CSS loaded by Dash |

## Layered API stack

Every static-figure module in MatterVis follows the same three-layer
shape. When a convenience wrapper does not fit your case, drop one
layer down rather than monkey-patching the wrapper. The arrows below
are "is composed from", not "must call".

```mermaid
flowchart TD
    subgraph WRAP["Convenience wrappers (one-shot figures)"]
        W1["build_cube_figure"]
        W2["lattice_compass_annotations"]
        W4["build_ortep_panel_figure"]
    end
    subgraph BLOCK["Composable trace / annotation builders"]
        B1["orbital_mesh_traces"]
        B2["canonical structure scene"]
        B4["paper_arrow_annotations"]
        B5["ortep_atom_mesh_traces"]
    end
    subgraph MATH["Pure math primitives (no Plotly objects)"]
        M1["read_cube"]
        M2["camera_screen_basis"]
        M3["project_to_screen"]
        M4["ellipsoid_principal_axes"]
    end

    W1 --> B1
    W1 --> B2
    W2 --> B4
    W4 --> B5
    W1 --> M1
    B4 --> M2
    B4 --> M3
    B5 --> M4
```

The scene API (`build_figure`) follows the same shape; see
[`scene_api.md`](scene_api.md) for its own pipeline diagram.

Caller heuristic: start at the top. If a wrapper hard-codes something
you need to change, the layer below exposes the same primitives the
wrapper uses internally — recompose them in your script rather than
adding a kwarg to the wrapper.

## Cross-cutting conventions

These hold for every consumer, regardless of which API surface you use.

- **Journal-agnostic library.** Typography, dpi, column widths, and
  palette choices for a specific journal are the **caller's**
  responsibility. Keep them in your own `*_style.py` next to your
  scripts. Do not patch `mat_viewer` to embed journal-specific
  defaults; this library is shared across projects.
- **Override styling at the call site.** Every wrapper that hard-codes
  a colour, font, anchor, or pixel offset also accepts those as keyword
  arguments. Defaults are conveniences, not commitments — pass kwargs
  rather than mutating module dicts (`ELEMENT_COLORS` etc.). Mutating
  module-level state breaks concurrent jobs.
- **Drop down a layer when the wrapper is too narrow.** The high-level
  helpers (`build_cube_figure`, `lattice_compass_annotations`, …) are
  convenience wrappers around
  exposed primitives (`orbital_mesh_traces`, `project_to_screen`, …).
  If the wrapper does not fit your case, compose the primitives
  directly instead of monkey-patching the wrapper.
- **Read the saved file back.** After every static export
  (`export_static`, `fig.write_image`, …) read the resulting PNG/PDF
  with the file-reading tool of your environment and walk the
  publication-figure-review checklist. Plotly + Kaleido fails silently
  on layout/transparency/legend issues; a successful exit code is not
  evidence of a correct figure.
