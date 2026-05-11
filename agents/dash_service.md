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
  `polyhedron_search_supercell` (`{a,b,c}` integer search radius
  used to discover ligands across periodic images for polyhedra; see
  `polyhedron_api.md`),
  `transforms` (full ordered list of structure mutations; see
  `transforms_api.md`),
  `supercell` (`{a,b,c}` shorthand that gets rewritten as a single
  `repeat` transform — overwrites any existing `repeat` transform
  rather than stacking),
  `bond_groups` (full list of bond-styling overrides; see
  `bond_groups_api.md`),
  `projection` (`"perspective"` / `"orthographic"`; mirrors the
  Plotly camera's `projection.type`. Setting it via `POST /state`
  has the same effect as `POST /camera/action {"action":
  "projection", "type": ...}`),
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
  `{"action": "reset"}`,
  `{"action": "align", "axis": "c"}`  (VESTA-style "look down lattice
  axis ``c``"; valid axes are `a`, `b`, `c`, `a*`, `b*`, `c*`),
  `{"action": "projection", "type": "orthographic"}`  (toggle the
  Plotly camera between ``perspective`` and ``orthographic``;
  mirrored onto ``state["projection"]`` so a subsequent ``GET
  /state`` echoes the choice).
  Both `align` and `projection` preserve the current zoom and the
  other half of the camera (alignment keeps projection, projection
  toggle keeps eye/center/up). Use them together to script a
  publication shot, e.g.
  `POST /camera/action {"action": "projection", "type":
  "orthographic"}` followed by `POST /camera/action {"action":
  "align", "axis": "c*"}`.
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
- `GET /atom_groups` / `POST /atom_groups` / `PATCH /atom_groups/{id}` /
  `DELETE /atom_groups/{id}` / `POST /atom_groups/reorder`
  Per-scene atom-group rules for colour / visibility / opacity /
  per-group material+style overrides. The Phase 2 replacement for
  the old binary `monochrome` flag. See
  [`atom_groups_api.md`](atom_groups_api.md). Selectors now also
  accept `labels`, `atom_indices`, `fragment_labels`, and
  `fragment_indices` (AND semantics across keys), so AI callers can
  pin a rule to a specific atom or fragment.
- `GET /bond_groups` / `POST /bond_groups` / `PATCH /bond_groups/{id}` /
  `DELETE /bond_groups/{id}` / `POST /bond_groups/reorder`
  Per-scene bond-styling rules. Selectors are `all`,
  `between_elements`, `labels` (label pairs), and `is_minor`.
  Per rule: `color`, `visible`, `opacity`, `radius_scale`. See
  [`bond_groups_api.md`](bond_groups_api.md).
- `GET /transforms` / `POST /transforms` / `PATCH /transforms/{id}` /
  `DELETE /transforms/{id}` / `POST /transforms/reorder`
  Ordered list of structure mutations applied before rendering:
  `repeat` (supercell), `grow_radius`, `grow_bonds`,
  `complete_fragment`, `complete_polyhedron`, `by_symmetry`, `slab`.
  See [`transforms_api.md`](transforms_api.md). The simplest case
  ("just give me a 2×2×2 view") can use the `supercell` shorthand on
  `POST /state` instead.
- `POST /polyhedra/{id}/instance_overrides/{fragment_label}`
  Body: `{"color": "#hex", "visible": true|false}`. Pins a single
  matched polyhedron (e.g. one specific Pb cluster) to a different
  colour or hides it without affecting the rest of the spec's tiling.
- `DELETE /polyhedra/{id}/instance_overrides/{fragment_label}`
  Removes a per-instance override and returns that polyhedron to the
  spec defaults. Both endpoints are documented in
  [`polyhedron_api.md`](polyhedron_api.md).
- `GET /screenshot`
  Returns a PNG snapshot of the current Plotly view.
- `POST /preset/save`
  Optional JSON body: `{"path": "custom_preset.json"}`.
- `POST /preset/load`
  JSON body: `{"path": "custom_preset.json"}`.
- `POST /export`
  Triggers the vendored `crystal_viewer.legacy.plot_crystal` exporter
  with the current preset.
- `GET /perf?since=N&limit=M`
  Tail of the in-process perf-event ring buffer. Returns
  `{"events": [...], "latest_seq": N, "log_path": "/tmp/cv-perf.log"}`.
  Each event is `{"seq", "ts", "iso", "kind", "label", "ms", "info"}`.
  `kind` is `cb` (Dash callback), `http` (REST handler), or `event`
  (sub-block of a longer operation, e.g. `loader:parse_asu` inside
  `upload:build_loaded_crystal`). Use `since=` to poll incrementally;
  the buffer keeps the last 1000 events. The full append-only log is
  also written to disk at `log_path` (override with the `CV_PERF_LOG`
  env var on the server). The Dash UI surfaces this as the bottom-right
  "Server log" panel; agents can use the endpoint directly to spot
  slow callbacks or expensive uploads without screen-scraping.
- `POST /perf/clear`
  Empties the in-memory ring buffer (the on-disk log is left alone).

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
- `topology-site-index`
- `topology-toggle` (single "Show polyhedra overlay" checkbox; the
  legacy `topology-species` checklist + `topology-hull-color` picker
  were removed in favour of the named-polyhedra table — see
  `polyhedron_api.md`)
- `polyhedra-add-btn`, `polyhedra-rows-container` (Phase 1 named
  polyhedra editor; per-row inputs are pattern-matched, see
  `polyhedron_api.md` for the `{type, spec_id}` shape)
- `atom-groups-add-btn`, `atom-groups-rows-container`,
  `atom-groups-preset-mono`, `atom-groups-clear-btn` (Phase 2 atom
  groups editor; per-row inputs are pattern-matched, see
  `atom_groups_api.md` for the `{type, group_id}` shape)
- `bond-groups-add-btn`, `bond-groups-rows-container` (Phase 4 bond
  groups editor; per-row inputs are pattern-matched with
  `{type, group_id}` shape, see `bond_groups_api.md`)
- `transforms-add-btn`, `transforms-rows-container`,
  `transforms-kind-select` (Phase 4 transforms editor; per-row
  inputs are pattern-matched with `{type, transform_id}` shape, see
  `transforms_api.md`)
- `polyhedron-search-supercell-{a,b,c}` (Phase 4 inputs that drive
  `polyhedron_search_supercell`; see `polyhedron_api.md`)
- `view-align-{a,b,c,astar,bstar,cstar}` and `view-reset` (Phase 4
  view tools — VESTA-style axis alignment buttons; clicking them
  fires the matching `POST /camera/action {"action": "align",
  "axis": ...}` on the backend)
- `view-projection` (Phase 4 view tools — `dcc.RadioItems` with
  values `perspective` / `orthographic`; mirrors
  `state["projection"]` and the camera's `projection.type` in both
  directions)
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

## Right-click / picking layer

The `crystal-graph` figure includes invisible `Scatter3d` "selection"
traces overlaid on atoms, polyhedra, and bond midpoints. Each marker
carries a `customdata` array whose first element is a kind tag, so
client-side code (the right-click menu, custom Plotly listeners,
external automation) can identify what was clicked without parsing
trace names:

| `customdata[0]` | Following fields |
|---|---|
| `"atom"` | `[atom_index, label, element, fragment_label]` |
| `"polyhedron"` | `[spec_id, fragment_label, is_anchor]` |
| `"bond"` | `[[label_i, label_j], [elem_i, elem_j], is_minor]` |

Promoting an ad-hoc click to a persistent rule is a one-shot REST
call:

- atom → `POST /atom_groups` with `selector={"labels": [label]}` or
  `selector={"atom_indices": [atom_index]}`.
- polyhedron → `POST /polyhedra/{spec_id}/instance_overrides/{fragment_label}`.
- bond → `POST /bond_groups` with
  `selector={"labels": [[label_i, label_j]]}` or
  `selector={"between_elements": [elem_i, elem_j]}`.

## WebSocket messages

- Server → client:
  `{"version": <int>, "state": {...}, "structures": [...]}`
  whenever state or structure inventory changes.
- Client → server:
  `{"type": "set_state", "payload": {...}}`

  Optional `"scene_id"` may be sent at the envelope level OR inside
  the inner `"payload"` to target a specific scene without disturbing
  the user's active tab. When omitted the patch lands on the active
  scene (legacy behaviour).

  ```json
  {"type": "set_state", "scene_id": "scene-2", "payload": {"atom_scale": 1.2}}
  ```

## Local-only data

- No CIF files are bundled in the repository.
- Default presets are written under `.local/`.
- Optional local catalog files can be provided via
  `catalog.local.json` or `.local/catalog.local.json`.
