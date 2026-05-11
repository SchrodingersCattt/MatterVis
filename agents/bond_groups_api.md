# Bond groups API

A **bond group** is a per-scene rule that overrides how matching
chemical bonds render: colour, visibility, opacity multiplier, and
radius multiplier. Bond groups mirror the shape and semantics of
`atom_groups` (`./atom_groups_api.md`); rules in the list apply in
order with later-wins semantics for overlapping bonds.

## Spec shape

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable identifier; auto-generated if not set. |
| `name` | string | Display label for the row. |
| `selector` | dict | What this rule matches; see grammar below. |
| `color` | str / null | CSS / hex colour applied to matching bonds. `null` keeps the renderer default. |
| `visible` | bool | `false` removes matching bonds entirely. |
| `opacity` | float | Multiplier in `[0, 1]` applied to bond opacity (the renderer default opacity is 1.0). |
| `radius_scale` | float | Multiplier on the bond cylinder/line radius. `1.0` is unchanged; `2.0` doubles thickness. |
| `enabled` | bool | `false` rules persist but do not paint. |

Empty `bond_groups` is the default and means "no overrides".
Rules apply on top of the canonical scene-level bond colouring.

## Selector grammar

A selector is a dict; missing keys are unconstrained. **All present
keys must match (AND semantics).**

| Key | Type | Behaviour |
|---|---|---|
| `all` | `true` | Matches every bond. Other keys are ignored when set. |
| `between_elements` | list[str] | The bond's endpoints must (set-wise) equal the two-element list. `["O","H"]` matches O-H **and** H-O bonds. A single-element list matches homo-element bonds (e.g. `["C"]` for C-C). |
| `labels` | list[list[str]] | Pairs of canonical atom labels. Each pair matches order-independently (`["Pb1","Cl3"]` == `["Cl3","Pb1"]`). |
| `is_minor` | bool | When `true`, only matches bonds whose `is_minor` flag is set (partial-occupancy bonds); when `false`, only major-occupancy bonds. |

Matching is exact and case-sensitive on element symbols and labels.

## REST surface

Endpoints are scoped to a single scene. The target scene is resolved
in this order:

1. `?scene_id=...` querystring on the URL.
2. `"scene_id"` field in the JSON body.
3. The currently active scene.

### `GET /api/v2/bond_groups`

```
{ "bond_groups": [ { ...spec... }, ... ] }
```

### `POST /api/v2/bond_groups`

Body: any subset of the spec shape; missing fields take their
defaults. Returns the persisted spec (with assigned id) on success.

```bash
curl -s -X POST http://localhost:50001/api/v2/bond_groups \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "O-H ghosts",
    "selector": {"between_elements": ["O","H"]},
    "color": "#888888",
    "opacity": 0.4,
    "radius_scale": 0.6
  }'
```

### `PATCH /api/v2/bond_groups/{group_id}`

Body: any subset of the spec; `id` cannot be changed. `404` for
unknown id.

### `DELETE /api/v2/bond_groups/{group_id}`

Returns `{"deleted": "<id>"}` on success, `404` when unknown.

### `POST /api/v2/bond_groups/reorder`

Body: `{"order": ["g1","g2",...]}`. Must reference every existing
group exactly once.

## Renderer contract

- Bond rules tag each bond dict with
  `_render_color`, `_render_visible`, `_render_opacity_scale`,
  `_render_radius_scale` before the renderer runs. The original bond
  attributes are restored after each render pass (the renderer
  wraps the mutation in `try…finally`), so subsequent invocations
  with different group lists do not see stale state.
- The renderer's `_bond_segments` skips invisible bonds, prefers
  `_render_color` over the canonical `color_i` / `color_j` halves,
  and yields the radius / opacity scales so `_bond_mesh_traces`
  buckets segments by `(color, is_minor, radius_bin, opacity_bin)`
  for trace efficiency.
- The figure-JSON cache key (`_cached_atom_bond_meshes`) extends with
  `bond_groups_cache_key(bond_groups)`. Any add / edit / reorder
  reliably re-renders.

## Picking integration

Each bond also gets an invisible `Scatter3d` selection trace at its
midpoint with `customdata = ["bond", [label_i, label_j],
[elem_i, elem_j], is_minor]`. The right-click menu uses this to
identify which bond the user clicked, and to offer
"Promote to bond group" actions that turn an ad-hoc selection into a
new bond group rule.

## Worked examples

```python
# Library use (no Dash app required).
from crystal_viewer.bond_groups import tag_bonds_with_groups

bond_groups = [
    {"id": "g1", "name": "weak", "enabled": True,
     "selector": {"between_elements": ["O", "H"]},
     "color": "#cccccc", "visible": True,
     "opacity": 0.5, "radius_scale": 0.6},
]
tag_bonds_with_groups(scene["bonds"], bond_groups)
```

```bash
# Recolour every Pb-Cl bond and shrink it.
curl -s -X POST http://localhost:50001/api/v2/bond_groups \
  -H 'Content-Type: application/json' \
  -d '{"name":"PbCl","selector":{"between_elements":["Pb","Cl"]},
       "color":"#ff7f0e","radius_scale":0.7}'

# Hide every minor (partial-occupancy) bond.
curl -s -X POST http://localhost:50001/api/v2/bond_groups \
  -H 'Content-Type: application/json' \
  -d '{"name":"hide minors","selector":{"is_minor":true},"visible":false}'
```

## Invariants

- `bond_groups_cache_key` is the canonical hashable summary of the
  list. It includes selector, colour, visibility, opacity, radius
  scale, and enabled flags; it excludes `id` and `name` so a row
  rename is a cheap re-paint.
- Disabled rows persist verbatim through the API. Toggling
  `enabled` is a one-field `PATCH`.
- Per-bond style fields (`_render_*`) are scratch space the renderer
  writes; downstream code MUST NOT depend on them outside a render
  pass.
