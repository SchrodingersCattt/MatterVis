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

## Selection / Appearance inspector

A viewport click opens **Selection / Appearance**. Its default scope is the
selected visible instances. Color, opacity, visibility, atom radius/material/
representation and surface edge controls are offered according to the shared
capability intersection. A mixed atom/surface selection cannot edit atom radius.
Changing an input previews a sparse saved edit; **Apply**, **Undo**, each field's
inherit button and **Reset all local overrides** use the same override API.

The scope menu can edit a containing molecule/disorder part, all source copies,
those copies under the same symmetry operation, one polyhedron spec, facets with
the same signed Miller index, or the whole morphology. **Select edited target** promotes a parent scope into the persistent selection;
**Back to picked child** returns to the original pick. Scope expansion is a
read-only preview until an appearance edit is applied. It enumerates concrete
IDs and shows the affected count. A source scope does not silently become the
default for later instance edits.

`Alt`-click cycles the depth-valid targets at the pixel. Opaque objects cut off
objects behind them; translucent surfaces allow cycling to objects underneath.
Ties use kind priority and canonical identity, independent of trace order.
Shift-drag selects the visible atom centres in the rectangle using the same
live camera as individual picks. Orbit/pan gestures and cancelled pointers do
not produce clicks.

### API examples

```python
# Python: sparse intent, independent of transient selection.
backend.set_style_override(atom_id, "atom", {"color": "#aa6688", "opacity": 0.6})
backend.reset_style_override(atom_id, field="color")  # latest inherited color
backend.set_style_overrides(targets, {"opacity": 0.5})  # validates intersection
report = backend.inspect_appearance(targets)
preview = backend.expand_selection_scope(targets[0], "source", commit=False)
```

`POST /api/v2/selection/appearance` accepts `{"targets": [...]}` and returns
`items` (target, effective fields, explicit overrides, affected count and
supported fields), the common `supported_fields`, `target_revision`, and
`discarded_targets`. It does not mutate selection. `POST /selection/scope`
also accepts `commit: false` for read-only scope previews.

A style PATCH may include `target_revision` from the inspector report. A changed
structure/display identity returns HTTP 409 and asks the caller to pick again.
Cosmetic selection changes do not invalidate this revision. Structure changes
clear old source-ordinal edits, even if the new structure also has an atom with
source index zero. Removed target IDs are reported by the appearance endpoint
and `GET /style_overrides` rather than reassigned to another object.

### One per-property inheritance contract

| Layer | Atom / bond | Surfaces |
|---|---|---|
| Palette and renderer defaults | Element/property colors and scene material | Existing geometry, topology and BFDH defaults |
| `entity_defaults` | Optional per-kind sparse defaults | Optional per-kind sparse defaults |
| Ordered rules | `atom_groups` / `bond_groups`, later matching fields win | Explicit geometry fields, polyhedron spec/instance paint, BFDH scene palette |
| Declared parent overrides | Molecule, then disorder part | Whole morphology, then facet |
| Exact target override | Exact displayed atom or bond ID | Polyhedron instance, facet ID or caller geometry ID |
| Interaction emphasis | Browser-local neutral contour | Browser-local neutral edges/contour |

Fields inherit independently. An explicit solid color takes priority over a
continuous property color map on that target; resetting Color restores the
current property map/group/palette, without restoring an old snapshot. A child
color override can coexist with inherited part opacity. No-op edits neither
create a color snapshot nor schedule an extra state update.

`entity_defaults` is per-scene Display intent, for example
`{"atom":{"opacity":0.8},"geometry":{"edge_width":1.5}}`. The existing global
palette/style controls and explicit group/spec fields retain their established
contracts. Unsupported fields are rejected on mutation and removed from
malformed persisted overrides. In particular generic Plotly meshes and bonds
do not expose a per-object `material` switch; atoms support `mesh`/`flat` and
the existing `ball`, `ball_stick`, `stick`, `ortep`, `wireframe` representations.

Caller mesh entities can also be persisted as `geometry_entities` through
`POST /api/v2/state`, using the same mesh payload as the Python geometry API.
They remain Display data. Styling, selection and serialization do not edit
coordinates, connectivity, source molecules or topology results.

### Rendering and exports

The browser projects visible atom radii, complete bond segments and mesh
triangles into a screen grid using Plotly's current model/view/projection
matrices. Triangle ownership remains intact in batched polyhedra and BFDH
facets. Hover performs no REST/Dash request and never restyles base materials.
A single pointer-transparent canvas carries the temporary neutral contours.
Graph replacement, scene switches and camera changes invalidate this index.

Saved overrides are honored by `build_figure(scene, style)` and the Plotly
`render(scene, style).save(...)` / existing CLI preset route. Static exports
omit picking traces, triangle ownership maps and selection metadata. Direct
`build_figure(..., include_interaction_traces=False, style={..., "show_selection":
True})` can explicitly request a diagnostic selection outline; the save API's
`include_interaction_traces=True` remains the diagnostic escape hatch.

Paint edits use the existing asynchronous frame/trace pipeline and cached
coordination geometry. They do not run topology in pointer callbacks or replace
camera ownership. Representation edits may rebuild atom render partitions.
