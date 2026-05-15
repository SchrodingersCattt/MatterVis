# Structure transforms API

The viewer's **transforms** model is the per-scene structure-mutation
pipeline ("grow", "repeat", "complete fragment", "slab", ...).
Each transform takes a base scene and produces a new scene whose
`draw_atoms`, `bonds`, and `fragment_table` reflect the operation
applied. Transforms compose in **list order**: each entry's input is
the output of the previous one.

## Display-Mode Interaction

Transforms operate on the scene that will be rendered. A fresh scene
often starts in `display_mode="formula_unit"`, which intentionally
trims the structure before rendering. That is not a useful input for
geometry-expanding transforms such as `repeat`, `grow_radius`,
`grow_bonds`, `complete_fragment`, `complete_polyhedron`,
`by_symmetry`, or `slab`.

When a transform is added through `POST /api/v2/transforms` while the
scene is still in `formula_unit`, MatterVis now auto-promotes the
scene to `unit_cell` by default and echoes:

```json
{
  "display_mode_auto_promoted": "formula_unit -> unit_cell",
  "warnings": ["display_mode=formula_unit trims transform output; ..."]
}
```

Pass `?auto_promote=false` to keep the old behaviour; the response will
still include a warning. Molecular-crystal slab cuts also return a
warning because they can slice covalent fragments and are primarily
intended for extended inorganic solids.

This file is the contract for that surface. Renaming an endpoint or
changing the persisted spec shape is a back-incompatible change —
bump the API version per `../AGENTS.md`.

## Spec shape

Every transform is a flat dict with these fields:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable identifier; auto-generated if not provided. |
| `name` | string | Display label for the row. Defaults to a friendly version of `kind`. |
| `kind` | string | **Required.** One of `repeat`, `grow_radius`, `grow_bonds`, `complete_fragment`, `complete_polyhedron`, `by_symmetry`, `slab`. |
| `params` | dict | Kind-specific parameter dict; see below. |
| `enabled` | bool | `false` rows persist but are skipped at apply time. |

### `params` schema per `kind`

#### `repeat`

```json
{"a": Na, "b": Nb, "c": Nc}
```

`Na`, `Nb`, `Nc` are the supercell counts along each lattice
direction. Always `>= 1`; `0` / negative values silently clamp to `1`.
Replicated atoms get a label suffix `[na,nb,nc]` so labels stay
unique while the home-cell `(0,0,0)` replica keeps its original
labels untouched (so `atom_groups` rules and click handlers continue
matching against the canonical names).

#### `grow_radius`

```json
{"seeds": <selector>, "radius": float}
```

For each seed atom, pulls in every atom (across periodic images)
whose Cartesian distance is `<= radius` Å. The home-cell seed atoms
themselves are kept (`include_seeds=True`); other home-cell atoms
inside the radius come along for the ride too — Diamond's "Grow"
includes them and a "grow" that didn't would feel broken.

#### `grow_bonds`

```json
{"seeds": <selector>, "hops": int}
```

Bond-walks `hops` steps outward from each seed. The bond detector
re-runs on the seed-plus-halo to discover periodic-image neighbours
that the home-cell bond table never connected. Use this to add a
single coordination shell while pulling in only the chemically
bonded atoms (vs `grow_radius` which is geometry-only).

#### `complete_fragment`

```json
{"seeds": <selector>, "max_hops": int}
```

Pulls in every atom transitively bonded to any seed across cell
boundaries. `max_hops` (default 32) is a safety net so accidentally
seeding a covalent crystal doesn't blow the atom count.
This is what you usually want when a CIF wraps a molecular cluster
across the unit-cell boundary and you want the molecules whole.

#### `complete_polyhedron`

```json
{"seeds": <selector>, "cutoff": float}
```

Geometry-only neighbour pull (alias of `grow_radius`). Use this to
"close" a coordination polyhedron when the home-cell fragment table
only kept some of the ligands (e.g. a Pb atom near a face whose Cl
neighbours sit in the next image). Chemistry-aware neighbour-typing
lives in `crystal_viewer.topology` and is outside this transform.

#### `by_symmetry`

```json
{
  "seeds": <selector>,
  "ops": [
    [[[r11, r12, r13], [r21, r22, r23], [r31, r32, r33]], [tx, ty, tz]],
    ...
  ]
}
```

Applies each `(R_frac, t_frac)` symmetry operation (in fractional
coordinates) to each seed. Use this to expand a partial occupancy /
asymmetric unit to its full multiplicity by hand. Identity is **not**
skipped automatically; if you want only the symmetry-related extras,
remove the seed atoms from the result yourself.

#### `slab`

```json
{
  "miller": [h, k, l],
  "layers": int|null,
  "min_thickness": float|null,
  "vacuum": float
}
```

Delegates to
`molcrys_kit.operations.surface.generate_topological_slab`. Either
`layers` or `min_thickness` must be set (`layers=3` is the default
when both are omitted). `vacuum` (Å) is the gap added on top of the
slab so the periodic images don't collide. Slab atoms replace the
home cell entirely, and the cell parameters are reset to the slab
basis.

### `seeds` selector grammar

Mirrors `crystal_viewer.atom_groups`:

- `{"all": true}` — every atom in the scene.
- `{"labels": ["Pb1", "Cl3"]}` — exact label list (stable across
  earlier transforms in the pipeline).
- `{"indices": [0, 5]}` — 0-based atom indices.
- `{"elements": ["Pb"]}` — element symbol filter.

A `null` / empty selector matches **no atoms**. Callers MUST opt in
to "all" explicitly with `{"all": true}` to operate on the whole
scene.

### Pipeline semantics

- Empty list (`transforms: []`) is a no-op; the base scene is
  returned unchanged. The cache hit-rate of the no-transform path
  stays at 100% because the dispatcher short-circuits.
- Transforms run in list order; each receives the result of the
  previous. So `[repeat 2x2x2, complete_polyhedron seeds=Pb,
  cutoff=3]` first builds the supercell, then closes the
  coordination shell of every Pb in the supercell.
- A safety ceiling (`MAX_ATOMS_AFTER_TRANSFORM = 50_000`) caps the
  pipeline output. Exceeding it raises `ValueError` rather than
  silently producing an unrenderable scene.
- Each transform writes a `_transform_lineage` entry on the result
  scene so the right-click "what produced this atom?" UX (and
  debugging) can trace which transforms contributed.

## State integration

`GET /api/v2/state` returns `transforms: [...]` alongside the
existing keys. Empty list (default) is the legacy "no transforms"
behaviour. Use the dedicated CRUD endpoints below for incremental
edits; `POST /api/v2/state` honours the full `transforms` field for
replay-from-snapshot use cases.

### `supercell` shorthand

`POST /api/v2/state` also accepts a top-level `supercell` field as
a convenience for the most common case:

```json
{"supercell": {"a": 2, "b": 2, "c": 2}}
```

This is rewritten internally into a single `repeat` transform on
the `transforms` list, replacing any existing `repeat` transform
(so issuing the shorthand twice does NOT stack into a `4x4x4`).
`{"a":1, "b":1, "c":1}` is the canonical "go back to the home
cell" call: the shorthand drops any existing `repeat` instead of
appending a no-op. Use it from one-shot AI scripts; for multi-step
pipelines or non-default `name`/`enabled` fields, use the dedicated
transforms endpoints below.

## REST surface

All endpoints are scoped to a single scene. The target scene is
resolved in this order:

1. `?scene_id=...` querystring on the URL.
2. `"scene_id"` field in the JSON body.
3. The currently active scene (`/api/v2/scenes/active`).

### `GET /api/v2/transforms`

```
{ "transforms": [ { ...transform... }, ... ] }
```

### `POST /api/v2/transforms`

Body:

```json
{
  "name": "2x2x2",
  "kind": "repeat",
  "params": {"a": 2, "b": 2, "c": 2},
  "enabled": true,
  "id": "optional-stable-id"
}
```

`kind` is required. Unknown kinds return 400. Returns the persisted
transform on success.

### `PATCH /api/v2/transforms/{transform_id}`

Body: any subset of the transform shape. The `id` cannot be changed.
Returns the merged transform on success, `404` for unknown id.

### `DELETE /api/v2/transforms/{transform_id}`

Returns `{"deleted": "<id>"}` on success, `404` when unknown.

### `POST /api/v2/transforms/reorder`

Body: `{"order": ["t1", "t2", ...]}`. Must contain every existing
transform id exactly once. Returns the new ordered list.

## Worked examples

```bash
# Render a 2x2x2 supercell of the active scene.
curl -s -X POST http://localhost:50001/api/v2/transforms \
  -H 'Content-Type: application/json' \
  -d '{"kind":"repeat","params":{"a":2,"b":2,"c":2}}'

# One-shot equivalent via the state shorthand.
curl -s -X POST http://localhost:50001/api/v2/state \
  -H 'Content-Type: application/json' \
  -d '{"supercell":{"a":2,"b":2,"c":2}}'

# Grow every Pb by 4 Å, then complete the bonded fragments.
curl -s -X POST http://localhost:50001/api/v2/transforms \
  -d '{"kind":"grow_radius","params":{"seeds":{"elements":["Pb"]},"radius":4.0}}' \
  -H 'Content-Type: application/json'
curl -s -X POST http://localhost:50001/api/v2/transforms \
  -d '{"kind":"complete_fragment","params":{"seeds":{"all":true}}}' \
  -H 'Content-Type: application/json'

# (001) slab, 4 layers, 12 Å vacuum.
curl -s -X POST http://localhost:50001/api/v2/transforms \
  -d '{"kind":"slab","params":{"miller":[0,0,1],"layers":4,"vacuum":12.0}}' \
  -H 'Content-Type: application/json'

# Snapshot then re-fetch screenshot.
curl -s -o /tmp/after.png http://localhost:50001/api/v2/screenshot
```

## Library / renderer contract

For library callers driving `crystal_viewer.scene.build_bundle_scene`
directly (no Dash app), pass `transforms=[...]` as a kwarg:

```python
from crystal_viewer.loader import build_bundle_scene

scene = build_bundle_scene(
    bundle,
    display_mode="formula_unit",
    show_hydrogen=False,
    preset=preset,
    transforms=[
        {"id": "t1", "kind": "repeat",
         "params": {"a": 2, "b": 2, "c": 2}, "enabled": True},
    ],
)
```

The base (no-transform) scene is cached on the bundle; the
post-transform scene is cached on a separate
`_transformed_scene_cache` keyed on
`(display_mode, show_hydrogen, transforms_cache_key(transforms))`.
Toggling a transform's `enabled` flag is a hash-lookup, not a
recompute.

## Invariants

- The transform list is **ordered**; reordering changes the result.
  The `reorder` endpoint and `_normalize_transforms` both preserve
  list order verbatim.
- `transforms_cache_key` is the single source of truth for the cache
  key. It includes `kind`, `enabled`, and sorted `params` keys; it
  excludes `id` and `name` so a row rename does not invalidate caches.
- The renderer's polyhedron geometry cache (`_topology_state_cache`)
  also keys on `transforms_cache_key`, so changing a transform reliably
  re-runs polyhedron analysis on the new atom set.
- Slab generation requires `bundle.crystal` to be a
  `MolecularCrystal` (set during `build_loaded_crystal`); structures
  loaded outside the standard catalog path may need to populate this
  attribute manually.
