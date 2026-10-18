# MatterVis Selection API

Selection is a per-scene working set of semantic targets. It is temporary UI
state, separate from persistent `atom_groups` and from the saved appearance
overrides. Legacy atom-label fields remain in the payload for compatibility,
but a browser or Python caller that needs an exact displayed object must use
`targets`.

## State Shape

```json
{
  "atom_labels": ["Cl1", "O3"],
  "active_label": "O3",
  "order": ["Cl1", "O3"],
  "targets": [
    {
      "kind": "atom",
      "id": "atom:source:3|image:1,0,0",
      "label": "Cl1",
      "element": "Cl",
      "source_index": 3,
      "image_shift": [1, 0, 0]
    }
  ],
  "active_target": {"kind": "atom", "id": "atom:source:3|image:1,0,0"}
}
```

Target IDs are stable semantic identities, never Plotly trace indexes. The
supported kinds are `atom`, `bond`, `molecule`, `part`, `polyhedron`, `facet`,
`morphology`, and `geometry`. Geometry meshes may add `subentity_id` for a
face. Atom IDs retain source index, periodic image, and disorder component;
two visually identical labels therefore remain independent targets.

## REST

All endpoints live under `/api/v2` and accept optional `scene_id`.

| Method | Path | Body | Description |
|---|---|---|---|
| `GET` | `/selection` | | Current selection. |
| `POST` | `/selection` | `{"atom_labels":["Cl1"],"replace":true}` | Replace or add labels. |
| `POST` | `/selection/targets` | `{"targets":[{"kind":"atom","id":"..."}]}` | Replace the exact semantic target set. `additive` and `toggle` are supported. |
| `GET` | `/selection/targets` | | Read the exact target set. |
| `POST` | `/selection/scope` | `{"target":{...},"scope":"source"}` | Expand one target to an explicit scope (`instance`, `source`, `symmetry`, `molecule`, `part`, `spec`, or `facet_family`). |
| `PATCH` | `/selection` | `{"add":["O1"],"remove":["Cl1"]}` | Incremental edit. |
| `DELETE` | `/selection` | | Clear selection. |
| `POST` | `/selection/by_fragment` | `{"fragment_label":"A0"}` | Select all atom labels in one MCK fragment. |
| `POST` | `/selection/by_element` | `{"element":"Cl"}` | Select all visible labels of an element. |
| `POST` | `/selection/by_box` | `{"rect_pixels":[x0,y0,x1,y1],"viewport_size":[w,h],"commit":true}` | Pick visible atoms whose centres project into the rectangle. `commit: false` returns the hit without writing the scene selection (used by the hover tooltip). A rectangle at most 48 px on both axes keeps only the nearest centre. Hidden/group-filtered atoms are never phantom targets. |
| `POST` | `/selection/all` | | Select all visible atom labels. |
| `POST` | `/selection/invert` | | Invert against visible atom labels. |
| `POST` | `/selection/promote` | `{"name":"picked atoms","color":"#FFD24A"}` | Create an `atom_group` from the selection and clear it. |

## Interaction rules

- A plain click replaces the set. `Shift`/`Ctrl`/`Cmd` clicks add or toggle
  the exact target under the pointer. `Esc` clears the set and closes the
  context menu. Clicking empty scene space follows the same clear rule for
  every target kind.
- Right-click resolves the target at the pointer coordinate even when Plotly
  did not emit a preceding hover event. Left-click, right-click, hover, and
  the inspector use the same normalized target payload and scene/revision
  guard. Hover state is browser-local and never committed to the server.
- `scope=instance` is the safe default. Broader scopes are expanded to a
  concrete target list before they are committed, so the affected count is
  inspectable and a later redraw cannot silently alias a different instance.
- Selection is scoped by scene. A scene switch clears stale hover state but
  preserves each scene's own persistent target set. When a structure or
  display filter removes an entity, stale targets and their local style
  overrides are dropped during reconciliation.

## Appearance overrides

`PATCH /api/v2/style_overrides` stores only explicit fields for one canonical
target ID. The resolver applies fields in this order: theme palette, entity
kind defaults, ordered selector/group/spec rules, explicit target override,
then the ephemeral browser outline. Hover and selection never replace the
base material or saved colour. Use `reset: true` with an optional `field` to
return one property to its current inherited value; removing the whole
override restores all inherited fields live after a theme change.

Only properties supported by the target kind are accepted. For example,
`representation` is valid for atoms, while `edge_width` is valid for mesh,
polyhedron, facet, and morphology targets. Invalid combinations return HTTP
400 with an explanatory error instead of being silently persisted.

## Notes

- `atom_labels` is a compatibility projection. It can intentionally include
  several displayed copies; use `targets` for identity-sensitive workflows.
- Saved appearance is per-scene Display intent and round-trips through
  presets and `GET/POST /api/v2/state`. It is independent from transient
  selection state and does not mutate source coordinates or connectivity.
- `select_fragment` uses the fragment labels already generated from
  MolCrysKit molecule grouping. It does not re-run bond perception.

## Overlapping 3-D hits

Render traces carry a small `mv_pick_*` policy in `trace.meta`. Adapters that
receive more than one Plotly point for a pixel should pass those points through
`mat_viewer.render.picking.adapt_plotly_hit` and then
`resolve_hit_candidate` (or `resolve_hit` when only the target is needed).
Camera depth is authoritative: the nearest opaque atom, bond, polyhedron, or
mesh face cuts off candidates behind it. A transparent surface remains
pickable but does not occlude an object behind it. Missing depth is treated as
far away, and ties use semantic kind priority, pixel distance, and stable
target identity rather than trace order.

The invisible `*_selection` traces are marked as probes. They provide stable
IDs for surfaces that cannot expose per-face customdata, but never become an
occluder themselves. Thin bonds use a smaller pick tolerance than atoms and
faces so a broad marker cannot steal a click at an atom centre. The policy is
backend-neutral and can be reused by browser fixtures or a future native
renderer without depending on Plotly internals.
