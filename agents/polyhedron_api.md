# Polyhedron specs API

The viewer's "polyhedron centres" model is a per-scene **named-row
table** (Diamond-style). Each row pins a coordination polyhedron with
its own colour, restored across reloads via the scene store, and is
exposed both as state on `GET /api/v2/state` and as a dedicated CRUD
surface under `/api/v2/polyhedra`.

This file is the contract for that surface. Renaming an endpoint or
changing the persisted spec shape is a back-incompatible change — bump
the API version per `../AGENTS.md`.

## Spec shape

Every spec is a flat dict with these fields:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable identifier; auto-generated if not provided. |
| `name` | string | Display label for the row. Defaults to `center_species`. |
| `center_species` | string | **Required.** Stoichiometric formula key from `species_options(structure)` (`"N"`, `"ClO4"`, `"C6N2"`, ...). |
| `ligand_species` | string \| null | When set, the polyhedron neighbour pool is restricted to fragments matching this formula. `null` = legacy auto-derive (perovskite-style A↔X / B↔X / X↔A,B). |
| `color` | string | Hull / shell colour; six-digit hex (`#RRGGBB`). Auto-assigned from a colour-blind-friendly palette when omitted. |
| `enabled` | bool | `false` rows persist but are skipped at render time. |
| `instance_overrides` | object | **Phase 4.** Per-fragment override map: `{fragment_label: {color, visible}}`. Empty `{}` means every matched fragment inherits the spec-level colour and visibility. Keys are the fragment-table labels exposed in `topology_data["spec_results"][i]["overlays"][j]["center_label"]`. |

### State integration

`GET /api/v2/state` returns `polyhedron_specs: [...]` alongside the
existing `topology_*` keys. The relationship between the two is:

- **Empty list** (default for every fresh scene) → the renderer falls
  back to the legacy `topology_species_keys` + shared
  `topology_hull_color`. One synthesised hull per matching fragment,
  one shared colour, no per-row identity. This is the pre-Phase-1
  behaviour and the path the existing Dash UI checklist still drives.
- **Non-empty list** → the explicit named rows take over. The legacy
  `topology_species_keys` field is still readable but no longer
  influences rendering for that scene.

`POST /api/v2/state` honours `polyhedron_specs` directly (full
replacement semantics). Use the dedicated CRUD endpoints below when
you only want to mutate one row at a time — they go through the same
normalisation pipeline and broadcast a state change to every connected
client.

## REST surface

All endpoints are scoped to a single scene. The target scene is
resolved in this order:

1. `?scene_id=...` querystring on the URL.
2. `"scene_id"` field in the JSON body.
3. The currently active scene (`/api/v2/scenes/active`).

### `GET /api/v2/polyhedra`

```
{ "specs": [ { ...spec... }, ... ] }
```

Returns the persisted list (post-normalisation) for the resolved
scene. Includes `enabled: false` rows.

### `POST /api/v2/polyhedra`

Body:

```json
{
  "name": "DABCO ring",
  "center_species": "C6N2",
  "ligand_species": null,
  "color": "#FF6A00",
  "enabled": true,
  "id": "optional-stable-id"
}
```

`center_species` is required; everything else is optional. Returns the
persisted spec on success (HTTP 200), `400` with `{"error": "..."}`
when `center_species` is missing or the payload is malformed.

### `PATCH /api/v2/polyhedra/{spec_id}`

Body: any subset of the spec shape. Fields not in the body are kept.
The `id` cannot be changed by a PATCH (it's overwritten with the URL
segment).

Returns the merged spec on success, `404` for unknown `spec_id`, `400`
for an invalid patch (e.g. clearing `center_species`).

### `DELETE /api/v2/polyhedra/{spec_id}`

Removes the row. Returns `{"deleted": "<id>"}` on success, `404` when
the id is unknown.

### `POST /api/v2/polyhedra/reorder`

Body: `{"order": ["spec_a", "spec_b", ...]}`. The list must contain
every existing spec id exactly once, in the desired display order.
Returns `{"specs": [...]}` (the new ordered list) on success, `400`
on a missing/extra id.

### `POST /api/v2/polyhedra/{spec_id}/instance_overrides/{fragment_label}` (Phase 4)

Body: `{"color": "#RRGGBB", "visible": bool}` (both fields optional;
at least one must be supplied for the override to register). Stamps a
per-fragment override on the spec, overlaying the spec-level colour /
visibility for that single polyhedron. Returns the updated spec on
success, `404` for unknown `spec_id`. Mirrors the right-click "Set
this one cyan" workflow in the UI.

Posting an empty body (`{}`) clears the override (semantically
identical to `DELETE`).

### `DELETE /api/v2/polyhedra/{spec_id}/instance_overrides/{fragment_label}` (Phase 4)

Removes the override for one fragment. Returns the updated spec on
success, `404` for unknown `spec_id`. Unknown fragment labels are a
no-op (returns the spec unchanged with HTTP 200).

### `PATCH /api/v2/polyhedra/{spec_id}` with `instance_overrides` (Phase 4)

The existing PATCH endpoint accepts an `instance_overrides` field
that replaces the entire map in one call:

```json
{
  "instance_overrides": {
    "X0": {"color": "#FF0000"},
    "X3": {"visible": false}
  }
}
```

Use this when replaying a saved scene state in a single round trip
(AI agents, scene-store reloads). The per-fragment endpoints above
are for incremental edits.

## Worked example

```bash
# List specs in the active tab (initially empty).
curl -s http://localhost:50001/api/v2/polyhedra
# {"specs": []}

# Add two named polyhedra with explicit colours.
curl -s -X POST http://localhost:50001/api/v2/polyhedra \
  -H 'Content-Type: application/json' \
  -d '{"name":"NH4 anion cage","center_species":"N","color":"#FF0000"}'

curl -s -X POST http://localhost:50001/api/v2/polyhedra \
  -H 'Content-Type: application/json' \
  -d '{"name":"DABCO ring","center_species":"C6N2","color":"#0000FF"}'

# Recolour and disable the first.
curl -s -X PATCH http://localhost:50001/api/v2/polyhedra/<spec_id> \
  -H 'Content-Type: application/json' \
  -d '{"color":"#FF6A00","enabled":false}'

# Reorder.
curl -s -X POST http://localhost:50001/api/v2/polyhedra/reorder \
  -H 'Content-Type: application/json' \
  -d '{"order":["<id_b>","<id_a>"]}'
```

## Renderer contract

For library callers driving `crystal_viewer.renderer.build_figure`
directly (no Dash app), the renderer pulls per-spec colours from
`topology_data["spec_results"]`, where each entry has:

```python
{
    "spec_id": "...",
    "name": "...",
    "color": "#RRGGBB",
    "center_species": "...",
    "ligand_species": "..." | None,
    "overlays": [
        {
            "center_coords": [x, y, z],
            "shell_coords": [[x, y, z], ...],
            "distances": [float, ...],
            "is_analysis_anchor": bool,
        },
        ...
    ],
}
```

Exactly one overlay across all specs has `is_analysis_anchor=True`;
that overlay drives the right-hand histogram and results panel.
`topology_data["analysis_spec_id"]` mirrors which spec owns the
anchor.

When `spec_results` is absent (legacy callers, hand-built fixtures),
the renderer falls back to the single-colour
`style["topology_hull_color"]` path. Both `topology_background_traces`
and `topology_foreground_traces` keep painter caches keyed on the
per-spec colour tuple **plus** the per-overlay instance override
tuple (Phase 4) so toggling a colour or hiding a single fragment is
a cheap re-paint, not a geometry recompute.

Per-overlay instance overrides ride on each overlay dict as
optional `color` (`#RRGGBB`) and `visible` (bool, default `True`)
fields. The renderer buckets overlays by colour so two distinct
override colours produce two merged-mesh traces; a hidden overlay
contributes nothing to the trace list.

### Polyhedron picking (Phase 4)

The renderer adds an invisible `Scatter3d` marker layer named
`polyhedron-selection` whose `customdata` carries
`["polyhedron", spec_id, fragment_label, is_anchor_int]`. The
right-click menu reads this on `clickData` to identify which
polyhedron the user picked; library callers driving Plotly directly
get the same hook.

### `polyhedron_search_supercell` (Phase 4)

A per-state `polyhedron_search_supercell: [Na, Nb, Nc]` triple is a
**floor** on the lattice-image search range used by
`analyze_topology` / `extract_coordination_shell`. `[0, 0, 0]`
(default) keeps the cutoff-driven span -- this is the legacy
behaviour. `[1, 1, 1]` extends the search by one image cell on each
side so polyhedra wrap to neighbouring images even without a
display-side supercell transform. The setting is decoupled from any
`repeat` transform applied via `transforms`; the two combine
multiplicatively (more images visible, more search range applied).
