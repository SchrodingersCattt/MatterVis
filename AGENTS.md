# MatterVis — Developer Contract

This document is for agents and humans **modifying** the MatterVis
codebase. If you are **calling** MatterVis from a script or HTTP
client, read [`agents/README.md`](agents/README.md) instead.

## Repository layout

```
MatterVis/
├── AGENTS.md            ← you are here (developer contract)
├── README.md            ← user-facing pitch
├── agents/              ← caller-facing API contracts (REST, programmatic)
├── crystal_viewer/      ← the library + Dash app
│   ├── api.py           ← REST handlers
│   ├── app.py           ← Dash UI bindings (callbacks, layouts)
│   ├── atom_groups.py   ← per-scene atom styling rules
│   ├── bond_groups.py   ← per-scene bond styling rules
│   ├── compass.py       ← camera-projected paper-coord indicators
│   ├── cube.py          ← static cube/orbital figures (I/O, isosurfaces, atoms, bonds)
│   ├── ideal_polyhedra.py
│   ├── legacy/          ← vendored matplotlib pipeline; do not extend
│   ├── loader.py        ← structure ingestion
│   ├── presets.py
│   ├── scenes.py        ← tab/session scene state
│   ├── ortep.py         ← thermal ellipsoid geometry + traces
│   ├── renderer.py      ← `build_figure` Plotly assembly + `uniform_viewport`
│   ├── scene.py         ← cell/cluster scene builder
│   ├── topology.py      ← coordination polyhedra geometry & analysis
│   └── transforms.py    ← supercell / grow / slab structure mutations
├── docs/                ← sphinx sources, score tables
├── scripts/             ← runnable scripts that exercise the public API
│   └── private/         ← local/private analysis scripts; keep unpublished data ignored
```

When adding a new feature, the module it belongs in is almost always
already on this list. Stretch the layered design (see below) before
introducing a new top-level module.

## Library principles

These are non-negotiable design constraints. Patches that violate them
should be rejected and rewritten.

1. **Journal-agnostic.** No journal name (`jacs`, `nature`, `acs`, …)
   ever appears in module, class, function, parameter, or asset names
   under `crystal_viewer/`. Journal style — typography, dpi, palette
   choices, column widths, ticking conventions — is the **caller's**
   responsibility. Style modules belong next to user scripts (e.g.
   `jacs_style.py` in a project folder), not in this repo.
2. **Layered API.** Pure-math primitives at the bottom (e.g.
   `camera_screen_basis`, `project_to_screen`, `read_cube`),
   composable building blocks above them (e.g.
   `paper_arrow_annotations`, `orbital_mesh_traces`), and
   convenience wrappers at the top (e.g.
   `lattice_compass_annotations`, `build_orbital_panel_figure`). A
   caller that outgrows the wrapper must be able to drop one layer
   down without re-implementing the math.
3. **Caller-overridable styling.** Every wrapper that hard-codes a
   colour, font, anchor, pixel offset, or arrow width must also
   accept those as keyword arguments. Defaults are conveniences, not
   commitments.
4. **No mutable module-level state.** Palettes (`ELEMENT_COLORS`
   etc.) are exposed for reading. Callers MUST NOT mutate them; the
   only supported override path is per-call kwargs. Mutating module
   dicts breaks concurrent jobs and silently corrupts unrelated
   render pipelines.
5. **Static export must be visually verified.** If you change any
   rendering code, regenerate the relevant `scripts/` figure and
   open the saved PNG/PDF. Plotly + Kaleido fails silently on
   layout/transparency/legend issues; a clean exit code does not
   imply a correct figure.

## Invariants the library promises to callers

When modifying a module, the following must remain true. Each item is
also documented (in caller terms) under `agents/`; if you intend to
break one, it is a public-API change — bump the version and update
both files.

### `crystal_viewer.cube` — see [`agents/cube_api.md`](agents/cube_api.md)

- Default trace order in `build_orbital_panel_figure` is
  `("cell", "orbital", "bonds", "atoms")`. Half-transparent
  isosurfaces composite under opaque atoms/bonds.
- `orbital_mesh_traces` accepts both `min_volume_voxels` and
  `atom_mask_radius` (with `extra_atom_positions`) so callers can
  clean up tiled-cube background noise. Both filters MUST remain
  available as kwargs; do not remove either or merge them into one.
- `atom_sphere_traces` and `bond_traces` emit fully opaque
  (`opacity=1.0`) Mesh3d with `ambient ≥ 0.75`. Do not lower these
  defaults — doing so reintroduces alpha-stacking ghosting (atoms
  behind orbitals fade to white in dense panels).
- `orbital_mesh_traces` exposes `opacity` as a kwarg. The default
  must remain ≥ 0.95 so dense orbitals do not produce washed-out
  panels via cumulative `(1−α)^N` blending.
- `sign_legend_annotations` uses unicode `\u25A0` / `\u2212`.
  HTML entities corrupt SVG export.
- `go.Isosurface` is the interactive-only fallback. The static
  publication path is marching-cubes Mesh3d via `use_mesh=True`
  in `export_static`.

### `crystal_viewer.compass` — see [`agents/compass_api.md`](agents/compass_api.md)

- Four-layer API (`camera_screen_basis` → `project_to_screen` →
  `paper_arrow_annotations` → `lattice_compass_annotations`). Do not
  collapse layers; downstream callers compose at every layer.
- All styling parameters of `lattice_compass_annotations` are kwargs
  with sensible defaults; never hard-code journal-specific palettes.
- Annotation arrows must use `axref="pixel"` / `ayref="pixel"`
  (Plotly rejects `"paper"` for arrow tail refs). Pixel y-axis
  points DOWN — flip the sign on `ay` when computing it from a
  screen-up direction.

### `crystal_viewer.app` polyhedron specs — see [`agents/polyhedron_api.md`](agents/polyhedron_api.md)

- Coordination polyhedra are a per-scene named-row table
 (`state["polyhedron_specs"] = [{id, name, kind, center_species,
 ligand_species, search_cutoff, color, enabled,
 instance_overrides}, ...]`). Each spec carries a `kind` field:
 `"fragment"` (legacy fragment-graph polyhedra) or `"atom"`
 (MolCrysKit-driven atom-centred polyhedra; ClO4 tetrahedra etc.).
 The Phase 5 default for a fresh scene is the chemistry-suggested
 atom-centred specs from
 `crystal_viewer.topology.suggest_default_polyhedron_specs` (driven
 by a `(central, ligand, max_bond)` table covering perchlorate,
 sulfate, nitrate, phosphate, halide / oxide perovskites, halide-
 bridged TM complexes). When that suggestion is empty (pure organic
 crystal etc.) the renderer falls back to the legacy
 `topology_species_keys` + shared `topology_hull_color` path.
- The renderer paints **per-spec colour** by reading
 `topology_data["spec_results"][i].color` directly, not
 `style["topology_hull_color"]`. The single-colour path remains as a
 back-compat fallback when `spec_results` is absent.
- `_topology_state_cache` is keyed on geometry-only fields
 (`(structure, display_mode, hydrogens, site_index, cutoff,
 spec_geometry_key)` where `spec_geometry_key` only carries
 `(center_species, ligand_species)` per spec). Per-spec colour is
 NOT in the geometry cache key — it lives on the renderer's painter
 cache instead, so swapping colours stays a cheap re-paint.
- `analyze_topology` / `extract_coordination_shell` accept an
 optional `ligand_species` keyword. When set, the neighbour-pool
 cache is partitioned per ligand restriction; do not collapse the
 cache key back to `(center_index, cutoff)` or two specs sharing a
 centre but differing in ligand will poison each other's pool.
- `analyze_topology` / `extract_coordination_shell` also accept
 `search_supercell=(na, nb, nc)`. This is the *neighbour-search*
 supercell — independent of the display supercell from the
 `repeat` transform. The neighbour-pool cache key includes it; do
 not collapse the key back to ignore it or callers that grow the
 search range will silently see stale (cached) hulls. Per-spec
 colour and per-instance overrides are NOT in this geometry cache
 key — they live on the renderer painter cache.
- Polyhedron specs may carry `instance_overrides`, a dict keyed on
 `fragment_label` whose values are partial style dicts
 (`{"color": ..., "visible": ...}`). The renderer applies these
 per-overlay in `topology_background_traces` and
 `topology_foreground_traces`; the geometry cache key does NOT
 include them, so flipping an instance is a cheap re-paint.
- REST surface `/api/v2/polyhedra` (CRUD + reorder, plus
 `instance_overrides/{fragment_label}` POST/DELETE) is part of the
 public API; back-incompatible changes require an API version bump.

### `crystal_viewer.atom_groups` + renderer — see [`agents/atom_groups_api.md`](agents/atom_groups_api.md)

- Atom-group rules are a per-scene list
 (`state["atom_groups"] = [{id, name, selector, color, color_light,
 visible, opacity, material, style}, ...]`). Rules apply in list
 order with later-wins semantics on overlapping atoms. Empty list
 = no overrides; the legacy `monochrome` flag is still honoured.
- `tag_atoms_with_groups` writes per-atom `_render_color`,
 `_render_color_light`, `_render_visible`, `_render_opacity_scale`,
 `_render_material`, `_render_style` fields. The renderer's
 `_atom_render_color` helper falls back to the element palette and
 the legacy `monochrome` flag when no rule overrode the atom; do
 NOT remove that fallback or pre-Phase-2 callers stop blackening.
- `_bond_segments` skips bonds whose endpoint atom has
 `_render_visible=False` and uses the per-atom render colour for the
 bond half. Do not collapse this back to `bond["color_i"]` directly.
- The renderer dispatcher partitions visible atoms by
 `(effective_material, effective_style)` and runs the matching
 trace builder per partition. Bonds stay scene-level; partitioning
 bonds across mixed (mesh,ortep) endpoints is intentionally not
 supported (mismatched per-atom materials would otherwise multiply
 trace counts and tank the figure JSON cache hit rate).
- The figure-JSON cache key in `_cached_atom_bond_meshes` extends to
 `_atom_groups_cache_key(atom_groups)` AND
 `bond_groups_cache_key(bond_groups)`. Editing or reordering either
 list must reliably re-render; keep the key in sync if you add new
 group fields.
- Atom-group selectors accept `all`, `elements`, `is_minor`,
 `labels`, `atom_indices`, `fragment_labels`, `fragment_indices`.
 Combining keys uses **AND** semantics; do not switch to OR or
 callers' "atoms named Pb1 in fragment X" rules silently broaden.
- REST surface `/api/v2/atom_groups` (CRUD + reorder) is part of the
 public API; back-incompatible changes require an API version bump.

### `crystal_viewer.bond_groups` + renderer — see [`agents/bond_groups_api.md`](agents/bond_groups_api.md)

- Bond-group rules mirror atom-group shape:
 `state["bond_groups"] = [{id, name, selector, color, visible,
 opacity, radius_scale, enabled}, ...]`. List order with
 later-wins on overlapping bonds.
- Selector grammar is `all`, `between_elements` (set-equal,
 order-independent), `labels` (list of label pairs, also
 order-independent within each pair), and `is_minor`. Combining
 keys uses **AND** semantics.
- `tag_bonds_with_groups` writes per-bond `_render_color`,
 `_render_visible`, `_render_opacity_scale`, `_render_radius_scale`.
 The renderer's `_bond_segments` skips invisible bonds, prefers
 `_render_color` over the per-half `color_i` / `color_j`, and
 yields `(radius_scale, opacity_scale)` so `_bond_mesh_traces`
 buckets segments by `(color, is_minor, radius_bin, opacity_bin)`.
 Do not collapse this back to `(color, is_minor)` — bond rules
 with non-default scales would otherwise stomp each other.
- The renderer wraps bond-group tagging in `try…finally` so the
 original bond dicts are restored after each render; downstream
 code MUST NOT depend on `_render_*` outside a render pass.
- REST surface `/api/v2/bond_groups` (CRUD + reorder) is part of the
 public API; back-incompatible changes require an API version bump.

### `crystal_viewer.transforms` + loader — see [`agents/transforms_api.md`](agents/transforms_api.md)

- Transforms are an ordered, list-shaped pipeline:
 `state["transforms"] = [{id, name, kind, params, enabled}, ...]`.
 Each transform consumes the previous one's output scene; the
 base (no-transform) scene is cached on the bundle, the
 post-transform scene on `_transformed_scene_cache` keyed by
 `(display_mode, show_hydrogen, transforms_cache_key(transforms))`.
- Supported `kind`s: `repeat`, `grow_radius`, `grow_bonds`,
 `complete_fragment`, `complete_polyhedron`, `by_symmetry`, `slab`.
 Add new kinds via `apply_one_transform` dispatch + a normaliser
 entry; never special-case kinds in `apply_transforms` itself.
- `replicate_atoms` keeps the home-cell `(0,0,0)` replica's labels
 unchanged and suffixes the new ones with `[na,nb,nc]`. Do not
 relabel the home replica or atom-group rules and click handlers
 stop matching the canonical names.
- `MAX_ATOMS_AFTER_TRANSFORM` is the safety ceiling — raise it only
 with a perf justification; the renderer is not free above ~50k.
- After atoms change, callers MUST run `rebuild_scene_with_atoms`
 (which re-detects bonds, recomputes bounds, regenerates fragment
 labels). Returning a scene with stale `bonds` or
 `_atom_fragment_labels` is what poisons polyhedron analysis.
- `transforms_cache_key` is the canonical hashable summary of the
 list. It includes `kind`, `enabled`, and sorted `params` keys; it
 excludes `id` and `name` so a row rename stays a cheap re-paint.
- The `polyhedron_search_supercell` field on the scene state lives
 in `topology_state_cache`'s key, NOT in `transforms_cache_key`,
 so changing the search range invalidates polyhedron geometry only,
 not the entire scene.
- REST surface `/api/v2/transforms` (CRUD + reorder) and the
 `supercell` shorthand on `POST /api/v2/state` are part of the
 public API; back-incompatible changes require an API version bump.

### `crystal_viewer.scene` / `renderer` — see [`agents/scene_api.md`](agents/scene_api.md)

- `display_mode="cluster"` skips formula-unit selection and PBC bond
  imaging. Bonds come purely from stored Cartesian coordinates;
  the 100 Å dummy cells that some CIF exporters emit around clusters
  are ignored.
- `apply_element_colors` is non-destructive: it returns a new scene
  or edits a single passed scene; it never mutates the module
  palette.
- `uniform_viewport` stamps a shared world-cube on a list of scenes
  so N-up grids render at one length-per-pixel.
- `build_figure` honours `show_title`, `axes_labels`,
  `element_colors`, `element_colors_light` style keys beyond the
  Dash defaults.
- `material`, `style`, and `disorder` are independent style axes:
  material chooses flat vs Mesh3d, style chooses ball/stick/ORTEP
  language, and disorder chooses how partial occupancy is marked.
  Do not re-couple disorder to opacity-only rendering.

### `crystal_viewer.ortep` — see [`agents/ortep_api.md`](agents/ortep_api.md)

- ORTEP has the same layered shape as other public APIs: pure math
  (`ellipsoid_principal_axes`, `ortep_mesh3d`), trace builders, then
  convenience wrappers.
- The default ORTEP probability is 50%; callers can override it per
  call with `ortep_probability`.
- `material="mesh", style="ortep"` renders real 3D ellipsoid meshes;
  `material="flat", style="ortep"` renders camera-facing publication
  billboards.

### Dash service — see [`agents/dash_service.md`](agents/dash_service.md)

- REST surface at `/api/v1` and the WebSocket schema documented in
  `agents/dash_service.md` are part of the public API.
  Back-incompatible changes require bumping the API version segment
  (`/api/v1` → `/api/v2`) and updating that file.
- The Stable UI element IDs listed in `agents/dash_service.md` are
  scraped by external automation. Do not rename without an
  announcement.

## Tests, lint, build

- Unit tests: `pytest tests/`.
- Examples regression: `python scripts/<n>_*.py` regenerates the
  paired figure in `scripts/_outputs/`. Visually inspect before
  merging.
- Lint: `ruff check crystal_viewer/`.

## Adding a new module or symbol

1. Pick a name that describes the data or operation, not a journal
   or a project (`compass.py`, not `jacs_compass.py`;
   `paper_arrow_annotations`, not `jacs_lattice_arrows`).
2. Follow the layered structure: pure math → composable blocks →
   convenience wrappers. Add at the lowest layer that fits, then
   layer wrappers above.
3. Expose every styling parameter as a kwarg with a sensible default.
   Prefer keyword-only arguments for non-data parameters.
4. Add an entry to the appropriate file under `agents/`. If the
   feature does not fit any existing file, create a new one and link
   it from `agents/README.md`.
5. Add a runnable example under `scripts/` if the feature is a
   user-facing capability.
6. Add unit tests for the pure-math layer at minimum; add integration
   tests for wrappers when the behaviour is non-trivial (transparency
   ordering, kaleido export, …).

## When you change a public API

1. Update the matching `agents/*.md` file.
2. Update affected `scripts/` scripts.
3. If the change is back-incompatible, bump the API version and call
   it out in the PR description.
