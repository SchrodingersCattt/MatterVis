# Dash viewer HTTP/WebSocket service

The interactive Dash app exposes a REST + WebSocket API for driving the
running viewer programmatically — uploads, state changes, screenshots,
preset save/load.

- Base URL: `http://{host}:{port}/api/v2`
- WebSocket: `ws://{host}:{port}/api/v2/ws`

`/api/v1` is a deprecated active-scene shim for one transition release.

## REST endpoints

- `GET /state`
  Returns the full viewer state. Add `?scene_id=...` to target a
  non-active tab.
- `POST /state`
  Accepts any subset of:
  `structure`, `display_mode`, `atom_scale`, `bond_radius`,
  `material`, `style`, `disorder`, `minor_opacity`, `axis_scale`,
  `display_options`,
  `topology_species_keys` (list of stoichiometric formulas like
  `"C8N1"`, `"ClO4"`, `"N1"` -- one polyhedron per matching fragment
  for every key in the list, which gives a tiled view "for free"),
  `topology_site_index` (primary site for the histogram /
  results panel), `topology_enabled`, `topology_hull_color`,
  `fast_rendering`, `camera`, `cutoff`. `material` is `mesh` or
  `flat`; `style` is `ball`, `ball_stick`, `stick`, `ortep`, or
  `wireframe`; `disorder` is `opacity`, `dashed_bonds`,
  `outline_rings`, `color_shift`, or `none`.

  Legacy aliases that still work: `topology_fragment_type` (`"A"` /
  `"B"` / `"X"`) is translated to the matching list of species keys
  in the active scene, and `topology_show_all_sites: true` selects
  every species at once.
  `display_mode` accepts `formula_unit`, `unit_cell`, `asymmetric_unit`,
  or `cluster` (free molecular cluster — every parsed atom is drawn,
  no formula-unit trim, no periodic imaging of bonds).
- `GET /camera`
  Returns the current Plotly camera.
- `POST /camera`
  Sets the full Plotly camera directly.
- `POST /camera/action`
  Convenience camera controls. Examples:
  `{"action": "zoom", "factor": 1.15}`,
  `{"action": "orbit", "yaw_deg": 12, "pitch_deg": -6}`,
  `{"action": "pan", "dx": 0.05, "dy": -0.03, "dz": 0.0}`,
  `{"action": "reset"}`.
- `GET /scenes`
  Lists scene tabs and the active scene id.
- `POST /scenes`
  Creates a tab. Body: `{"structure": "DAP-4", "label": "view A",
  "state": {...}}`.
- `PATCH /scenes/{id}`
  Renames or patches a tab.
- `DELETE /scenes/{id}`
  Closes a tab.
- `POST /scenes/{id}/duplicate`
  Duplicates a tab.
- `POST /scenes/reorder`
  Body: `{"order": ["scene_a", "scene_b"]}`.
- `GET /scenes/active` / `POST /scenes/active`
  Reads or changes the active scene.
- `POST /upload`
  Multipart form upload with field `file`.
- `GET /structures`
  Lists the loaded catalog and uploaded structures.
- `GET /scene/{name}`
  Returns the scene JSON and fragment table.
- `POST /topology`
  JSON body: `{"structure": "SY", "center_index": 0, "cutoff": 10.0}`.
- `GET /polyhedra` / `POST /polyhedra` / `PATCH /polyhedra/{id}` /
  `DELETE /polyhedra/{id}` / `POST /polyhedra/reorder`
  Per-scene named-row table for coordination polyhedra. Each row pins
  a centre species + optional ligand species + colour + enabled flag.
  See [`polyhedron_api.md`](polyhedron_api.md) for the spec shape and
  worked examples.
- `GET /screenshot`
  Returns a PNG snapshot of the current Plotly view.
- `POST /preset/save`
  Optional JSON body: `{"path": "custom_preset.json"}`.
- `POST /preset/load`
  JSON body: `{"path": "custom_preset.json"}`.
- `POST /export`
  Triggers the vendored `crystal_viewer.legacy.plot_crystal` exporter
  with the current preset.

## Stable UI element IDs

Use these when scripting through Selenium / Playwright / Dash testing
hooks rather than the REST surface.

- `scene-tabs`: scene tab row
- `scene-tab-{id}`: individual scene tab
- `scene-tab-close-{id}`: per-tab close button when rendered
- `scene-new-tab-btn`: duplicate/new scene button
- `scene-tab-rename-input`: active scene rename input
- `scene-cif-upload`: upload zone
- `display-options`: labels / axes / minor-only / wireframe checklist
- `display-mode-selector`: `formula_unit`, `unit_cell`,
  `asymmetric_unit`, `cluster`
- `material-selector`: `mesh`, `flat`
- `style-selector`: `ball`, `ball_stick`, `stick`, `ortep`,
  `wireframe`
- `disorder-selector`: `opacity`, `dashed_bonds`, `outline_rings`,
  `color_shift`, `none`
- `atom-scale-slider`
- `bond-radius-slider`
- `minor-opacity-slider`
- `axis-scale-slider`
- `topology-fragment-type`
- `topology-site-index`
- `topology-toggle`
- `crystal-graph`
- `topology-histogram`
- `topology-results`
- `save-preset-btn`
- `export-btn`

## Suggested automation pattern

1. `POST /upload` with a CIF file.
2. `GET /scenes` to discover the uploaded scene tab id.
3. `POST /state?scene_id=...` to set render/display controls without
   disturbing the user's active tab.
4. `POST /topology?scene_id=...` with a chosen `center_index`.
5. `POST /camera/action?scene_id=...` to zoom/orbit/pan if needed.
6. `GET /screenshot?scene_id=...` to capture that scene.
7. `POST /preset/save` if the tuned state should be persisted.

## WebSocket messages

- Server → client:
  `{"version": <int>, "state": {...}, "structures": [...]}`
  whenever state or structure inventory changes.
- Client → server:
  `{"type": "set_state", "payload": {...}}`

## Local-only data

- No CIF files are bundled in the repository.
- Default presets are written under `.local/`.
- Optional local catalog files can be provided via
  `catalog.local.json` or `.local/catalog.local.json`.
