# Atom-group rendering rules API

The viewer's **atom-group** model is the per-scene replacement for the
old binary `monochrome` flag. It lets the caller pin a list of
selector → override rules onto a scene's state, restored across
reloads via the scene store, and exposed both as state on
`GET /api/v2/state` and via a dedicated CRUD surface under
`/api/v2/atom_groups`.

This file is the contract for that surface. Renaming an endpoint or
changing the persisted group shape is a back-incompatible change —
bump the API version per `../AGENTS.md`.

## Group shape

Every group is a flat dict with these fields:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable identifier; auto-generated if not provided. |
| `name` | string | Display label for the row. Defaults to a description of the selector. |
| `selector` | dict | **Required.** See selectors below. |
| `color` | string \| null | Hex `#rrggbb` colour for matched atoms (and the bond halves rooted on them). `null` = no override; the renderer falls back to the element palette (and to the legacy `monochrome` flag when set). |
| `color_light` | string \| null | Optional minor/disorder-side colour. When `color` is set and `color_light` is not, the minor side defaults to the same hue. |
| `visible` | bool | `false` hides every matched atom AND any bond touching one. |
| `opacity` | float \| null | Per-group opacity multiplier in `[0, 1]`. Replaces (does not stack-multiply) earlier matching groups. `null` = no override. |
| `material` | string \| null | `"mesh"` or `"flat"`; per-group atom-rendering material override. `null` = inherit scene-level material. |
| `style` | string \| null | `"ball"`, `"ball_stick"`, `"stick"`, `"ortep"`, or `"wireframe"`; per-group atom-rendering style override. `null` = inherit scene-level style. |

### Selector grammar

A selector is a dict whose keys are AND-combined. Any key that is
absent is ignored. The supported keys are:

- `{"all": true}` — match every atom in the scene.
- `{"elements": ["O", "S"]}` — match atoms whose element symbol
  appears in the list (string-equal, case-sensitive on whatever the
  loader puts in `draw_atoms[i]["elem"]`).
- `{"is_minor": true}` / `{"is_minor": false}` — match by the
  disorder major/minor flag.
- `{"labels": ["Pb1", "Cl3"]}` — **Phase 4.** Exact atom-label list.
  Stable across structure transforms (a `repeat 2x2x2` keeps the
  home-cell labels intact and assigns `[na,nb,nc]` suffixes to
  replicas, so `["Pb1"]` keeps matching the original Pb after
  growing the cell). The right-click "Set this one cyan" path
  emits this selector; AI scripts use it to recolour by label.
- `{"atom_indices": [0, 5]}` — **Phase 4.** 0-based indices into
  the rendered `draw_atoms`. Volatile across transforms — use
  `labels` for transform-stable identity. The renderer threads
  the index through `tag_atoms_with_groups`; library callers must
  do the same when calling `atom_matches_selector` directly.
- `{"fragment_labels": ["A0", "B1"]}` — **Phase 4.** Match every
  atom whose fragment-table label is in the list. Used by the
  per-instance polyhedron override pipeline (the renderer threads
  `scene["atom_fragment_labels"][idx]` through to the matcher).
- `{"fragment_indices": [2]}` — **Phase 4.** Same as
  `fragment_labels` but matches by integer fragment index. Accepts
  both bare integer (`5`) and digit-string (`"5"`) forms when the
  caller threads the label through.

Unknown keys are silently ignored. A selector that ends up with no
recognised keys is **rejected** at the normaliser layer
(`POST /api/v2/atom_groups` returns 400) — a no-op selector that
silently matches nothing was the most common Phase 2 footgun in
testing.

### Application order

Groups apply in **list order** with **later-wins** semantics on
overlapping atoms. So `[{all -> grey}, {elements: O -> red}]` paints
everything grey except oxygens, which come out red. Reorder via
`POST /api/v2/atom_groups/reorder` to change the priority cascade.

### Relationship to the legacy monochrome flag

The legacy `monochrome` flag (set via `display_options` or as a
top-level `monochrome=true` on a saved preset) is auto-migrated by
`ViewerBackend.normalize_state` into a single
`{"selector": {"all": true}, "color": "#000000"}` atom_group rule
the first time the patch carrying `display_options` is seen. After
that one migration the flag is **inert** — the renderer ignores
`style["monochrome"]` whenever `atom_groups` is non-empty, so a
caller who later adds a "red oxygens" rule does not get
double-applied "all-black except red O" without intending to.

The migration path for a "monochrome with red oxygens" figure is
therefore:

```bash
# legacy monochrome auto-migrates into an atom_group on the first
# normalize pass; you can also POST it explicitly:
curl -X POST /api/v2/atom_groups \
  -d '{"selector":{"all":true},"color":"#000000","name":"monochrome"}'

# then layer the red-O override on top (later rules win):
curl -X POST /api/v2/atom_groups \
  -d '{"selector":{"elements":["O"]},"color":"#FF0000"}'
```

The renderer's atom-trace builders consult `_render_color` first,
falling back to `_style_color(atom['color'], style)` only when no
group rule overrode the atom AND no atom_groups are set on the
scene at all. That's the only path through which monochrome
interacts with atom_groups.

### `material` / `style` partitioning

When a group sets `material` or `style`, the renderer partitions
`draw_atoms` by `(effective_material, effective_style)` and runs the
matching trace builder on each subset. So you can ask for "all
atoms ball-stick except O atoms as ORTEP ellipsoids" with one rule:

```json
{"selector": {"elements": ["O"]}, "material": "mesh", "style": "ortep"}
```

**Bonds are not partitioned.** Their material/style come from the
scene-level `material`/`style`. The bond endpoint *colour* still
respects per-atom `_render_color` so a recoloured atom and the
adjacent bond half stay visually consistent.

## REST surface

All endpoints are scoped to a single scene. The target scene is
resolved in this order:

1. `?scene_id=...` querystring on the URL.
2. `"scene_id"` field in the JSON body.
3. The currently active scene (`/api/v2/scenes/active`).

### `GET /api/v2/atom_groups`

```
{ "groups": [ { ...group... }, ... ] }
```

### `POST /api/v2/atom_groups`

Body:

```json
{
  "name": "oxygen-red",
  "selector": {"elements": ["O"]},
  "color": "#FF0000",
  "color_light": null,
  "visible": true,
  "opacity": null,
  "material": null,
  "style": null,
  "id": "optional-stable-id"
}
```

`selector` is required and must yield at least one recognised key.
Returns the persisted group on success (HTTP 200), `400` when the
selector is missing or unsalvageable.

### `PATCH /api/v2/atom_groups/{group_id}`

Body: any subset of the group shape. Fields not in the body are kept.
The `id` cannot be changed (it's overwritten with the URL segment).

### `DELETE /api/v2/atom_groups/{group_id}`

Returns `{"deleted": "<id>"}` on success, `404` when unknown.

### `POST /api/v2/atom_groups/reorder`

Body: `{"order": ["group_a", "group_b", ...]}`. Must contain every
existing group id exactly once. Returns the new ordered list.

## Worked examples

```bash
# Hide every hydrogen.
curl -s -X POST http://localhost:50001/api/v2/atom_groups \
  -H 'Content-Type: application/json' \
  -d '{"name":"hide H","selector":{"elements":["H"]},"visible":false}'

# Paint everything black except oxygens (red) and chlorines (green).
curl -s -X POST http://localhost:50001/api/v2/atom_groups \
  -d '{"selector":{"all":true},"color":"#000000"}' \
  -H 'Content-Type: application/json'
curl -s -X POST http://localhost:50001/api/v2/atom_groups \
  -d '{"selector":{"elements":["O"]},"color":"#FF0000"}' \
  -H 'Content-Type: application/json'
curl -s -X POST http://localhost:50001/api/v2/atom_groups \
  -d '{"selector":{"elements":["Cl"]},"color":"#00AA00"}' \
  -H 'Content-Type: application/json'

# Render H atoms as a fade-out (50% opacity) without hiding them.
curl -s -X POST http://localhost:50001/api/v2/atom_groups \
  -H 'Content-Type: application/json' \
  -d '{"selector":{"elements":["H"]},"opacity":0.5}'

# Render O atoms as ORTEP ellipsoids while everything else stays as
# the scene's ball-stick.
curl -s -X POST http://localhost:50001/api/v2/atom_groups \
  -H 'Content-Type: application/json' \
  -d '{"selector":{"elements":["O"]},"material":"mesh","style":"ortep"}'
```

## Renderer contract

For library callers driving `crystal_viewer.renderer.build_figure`
directly (no Dash app), atom_groups ride along on the style dict:

```python
style["atom_groups"] = [
    {"id": "...", "selector": {...}, "color": "...", ...},
    ...,
]
```

The dispatcher (`_cached_atom_bond_meshes`) detects a non-empty
`atom_groups` list, calls
`crystal_viewer.atom_groups.tag_atoms_with_groups(scene["draw_atoms"],
atom_groups)` to decorate every atom with `_render_color`,
`_render_color_light`, `_render_visible`, `_render_opacity_scale`,
`_render_material`, and `_render_style`, and partitions the result
by `(effective_material, effective_style)` to dispatch each subset
to the matching trace builder.

Cache keys at every layer extend to the atom_groups hash so a group
edit reliably re-renders, and a colour swap is a cheap re-paint.

The empty-list / never-set path is a fast no-op: the dispatcher takes
the same branch it always did, all renderer caches keep their old
keys, and the legacy `monochrome` flag still works untouched.
