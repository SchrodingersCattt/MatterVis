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
| `ligand_species` | string \| null | Explicit ligand formula for MolCrysKit molecule-level packing polyhedra. `null` persists but is not rendered; MatterVis no longer derives auto-ligand shells locally. |
| `color` | string | Hull / shell colour; six-digit hex (`#RRGGBB`). Auto-assigned from a colour-blind-friendly palette when omitted. |
| `enabled` | bool | `false` rows persist but are skipped at render time. |
| `enforce_enclosure` | bool | Packing-shell mode. `true` (default) keeps MolCrysKit's gap+enclosure expansion; `false` stops at the distance-gap shell. |
| `centroid_offset_frac` | number | MolCrysKit centering tolerance passed to `find_polyhedra`; default is `0.15`. Larger values make the enclosure check less strict. |
| `instance_overrides` | object | **Phase 4.** Per-fragment override map: `{fragment_label: {color, visible}}`. Empty `{}` means every matched fragment inherits the spec-level colour and visibility. Keys are the fragment-table labels exposed in `topology_data["spec_results"][i]["overlays"][j]["center_label"]`. |

### State integration

`GET /api/v2/state` returns `polyhedron_specs: [...]` alongside the
existing `topology_*` keys. Fresh scenes start with
`topology_enabled=false`, so no polyhedron overlay is computed until the
caller opts in. Empty `polyhedron_specs` means "render no named
polyhedra"; MatterVis no longer synthesises auto-ligand rows from
`topology_species_keys`. Non-empty explicit rows drive the overlay.

`POST /api/v2/state` honours `polyhedron_specs` directly (full
replacement semantics). Use the dedicated CRUD endpoints below when
you only want to mutate one row at a time — they go through the same
normalisation pipeline and broadcast a state change to every connected
client.

## Molecule vs Atom Analysis

Named polyhedron specs remain molecule-level packing shells:
`center_species` and `ligand_species` are fragment formula keys such as
`"C6N2"` and `"ClO4"`, and MolCrysKit compares molecular centroids.

For atom-level coordination polyhedra, call `POST /api/v2/topology`
with `level: "atom"` and element symbols:

```json
{"center_index": 0, "center_species": "Cl", "ligand_species": "O", "level": "atom"}
```

The response includes `analysis_level`, `coordination_polyhedron_label`,
`label_modifier`, and `cshm_value` (inside `shape`). Molecule-level
calls instead fill `packing_shell_label`. CShM values are comparable to
SHAPE-style continuous shape measures: `0` is ideal; small values
indicate clean/distorted matches, and large values are reported as
ambiguous or irregular by MolCrysKit's `label_modifier`.

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
  "enforce_enclosure": true,
  "centroid_offset_frac": 0.15,
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
    "enforce_enclosure": True,
    "centroid_offset_frac": 0.15,
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

### MolCrysKit Computation

Named polyhedra are computed by
`molcrys_kit.analysis.packing_shell.find_polyhedra(level="molecule")`.
MatterVis passes the spec's compact formulas (`"C6N2"`, `"ClO4"`, ...)
as MolCrysKit moiety strings (`"C6 N2"`, `"Cl O4"`, ...), the matched
MolCrysKit molecule index as `central_indices`, and the state `cutoff`
as the MolCrysKit `cutoff=` kwarg. **On `level="molecule"` (per
MolCrysKit PR #32), `cutoff=` is the candidate search radius feeding
`detect_coordination_number`'s gap+enclosure heuristic — _not_ a hard
"include every neighbour within X Å" cap.** That semantic is the right
one for MV's analysis card: the displayed CN is always the natural
first packing shell selected by gap+enclosure, regardless of how
generously the user widens the search radius.

The historical "fill the ball" mode (formerly the default in MCK
before PR #32) is still available in MolCrysKit via
`hard_cutoff=`, but MV intentionally does **not** plumb `hard_cutoff`
through `polyhedron_specs` — exposing a per-spec hard cap would make
two specs at the same `cutoff` disagree on what the polyhedron means,
which is exactly the footgun the gap+enclosure default is there to
prevent. If we ever want to surface the extended A--X12 perovskite
cuboctahedron (or any other "show me everything within X Å"
analysis), it should be a separate, explicitly-named spec field, not
overloaded onto `cutoff`.

MatterVis only applies display-coordinate offsets and renderer colours
to the returned `shell_coords`; it does not maintain a separate
fragment-centroid neighbour search.

The analysis-card payload (`topology_data["gap_info"]`) carries the
MCK record fields verbatim so callers can introspect the result:

| Field | Meaning |
|---|---|
| `mode` | `"gap+enclosure"` for MV's natural-shell calls; `"cutoff"` only when a future feature opts into hard-cap mode. |
| `search_cutoff` | The candidate search radius MV asked for (= state `cutoff`). |
| `hard_cutoff` | `null` for MV's natural-shell calls; echoes the MCK kwarg if a hard cap was applied. |
| `cutoff` | Echo of what `detect_coordination_number` received (i.e. the hard cap value, or `null`). Kept for back-compat; new code should prefer `hard_cutoff` to ask "was a hard cap applied?". |
| `primary_gap_cn`, `gap_value`, `gap_index` | Where the natural distance gap fell in the candidate list. |
| `enclosed`, `enclosure_expanded` | Whether the chosen CN's hull encloses the centre, and whether the algorithm had to expand past the gap to make that true. |

### Polyhedron picking (Phase 4)

The renderer adds an invisible `Scatter3d` marker layer named
`polyhedron-selection` whose `customdata` carries
`["polyhedron", spec_id, fragment_label, is_anchor_int]`. The
right-click menu reads this on `clickData` to identify which
polyhedron the user picked; library callers driving Plotly directly
get the same hook.

