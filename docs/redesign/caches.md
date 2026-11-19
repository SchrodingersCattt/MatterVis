# Cache Contracts

The target design has explicit cache layers.  A cache key is part of the public
internal contract: every input that affects a value must either appear in the
key or be proven irrelevant.

## Cache Layers

The four cache layers form a strict dependency chain: each layer's value is
derived from the next one up plus a small set of extra inputs.  An input that
appears in a downstream layer's key but never feeds an upstream layer is a
signal that the upstream layer's key is too narrow.

```mermaid
flowchart TD
    inStruct["structure"]
    inDisplay["display_mode + show_hydrogen"]
    inTransforms["transforms_cache_key<br/>(kind, enabled, sorted params)"]
    inSite["topology_site_index"]
    inCutoff["cutoff"]
    inSpecs["spec_geometry_key<br/>(center_species, ligand_species,<br/>enforce_enclosure, centroid_offset_frac)"]
    inStateBody["Resolved state JSON<br/>(minus version, server_started_at, camera)"]

    cacheBase["Base Scene Cache<br/>LoadedCrystal.scene_cache<br/>key: (structure, display_mode, show_hydrogen)"]
    cacheTrans["Transform Scene Cache<br/>LoadedCrystal._transformed_scene_cache<br/>key: (display_mode, show_hydrogen, transforms_cache_key)"]
    cacheTopo["Topology Geometry Cache<br/>LoadedCrystal._topology_state_cache<br/>+ _analyze_topology_cache<br/>key: (structure, display_mode, show_hydrogen,<br/>transforms_key, site_index, cutoff, spec_geometry_key)"]
    cacheFig["Figure Body Cache<br/>ViewerBackend._figure_cache<br/>key: JSON of resolved state"]

    inStruct --> cacheBase
    inDisplay --> cacheBase
    cacheBase --> cacheTrans
    inTransforms --> cacheTrans
    cacheTrans --> cacheTopo
    inSite --> cacheTopo
    inCutoff --> cacheTopo
    inSpecs --> cacheTopo
    cacheTopo --> cacheFig
    inStateBody --> cacheFig
```

### Base Scene Cache

Current location: `LoadedCrystal.scene_cache`.

Target key:

\[
(\mathrm{structure}, \mathrm{display\_mode}, \mathrm{show\_hydrogen})
\]

Value: base scene before transforms, including selected atoms, bonds, labels,
bounds, fragment table, and atom-fragment labels.

Inputs that must not be in the key: colors, camera, labels visibility, axes
visibility, polyhedron colors.

### Transform Scene Cache

Current location: `LoadedCrystal._transformed_scene_cache`.

Target key:

\[
(\mathrm{structure}, \mathrm{display\_mode}, \mathrm{show\_hydrogen},
\mathrm{transforms\_cache\_key})
\]

Value: scene after the transform pipeline, plus transformed fragment table.

This cache is geometry-only.  Transform row `id` and `name` stay out of the key;
`kind`, `enabled`, and normalized `params` stay in.

### Topology Geometry Cache

Current location: `LoadedCrystal._topology_state_cache` and
`LoadedCrystal._analyze_topology_cache`.

Target key:

\[
(\mathrm{structure}, \mathrm{display\_mode}, \mathrm{show\_hydrogen},
\mathrm{transforms\_key}, \mathrm{site\_index}, \mathrm{cutoff},
\mathrm{spec\_geometry\_key})
\]

Value: shell coordinates, hulls, center coordinates, and spec geometry.

Colors and `instance_overrides` are painter inputs, not geometry inputs.

### Figure Body Cache

Current location: `ViewerBackend._figure_cache`.

Target key: JSON-safe resolved state excluding only:

- `version`
- `server_started_at`
- compatible `camera`

The figure body cache may reuse mesh and layout body data across camera drags,
but must refresh camera-dependent overlays such as compass annotations.

The stored value is `fig.to_plotly_json()` plus the topology wrapper. A
Mesh3d supercell makes one entry much larger than a molecule, so the cache
is an LRU with both an entry cap and a byte cap (`FIGURE_CACHE_MAX_ENTRIES`
and `FIGURE_CACHE_MAX_BYTES` in `mat_viewer.render.retention`). The newest entry
stays even when it alone exceeds the byte cap, which keeps a repeat of the
current style from rebuilding. Older slider ticks and idle tabs are the
entries that go first.

### Retention budgets

Entry count alone does not bound a long-lived server. The broadcast journal
used to keep 32 full figure bodies, the per-scene mesh map kept every style
key, and the base/transform scene maps kept every display mode and repeat.
Those layers now share the policy in `mat_viewer.render.retention`:

| Layer | Where | Caps |
| --- | --- | --- |
| Atom/bond mesh traces | `scene["_mesh_trace_cache"]` | 8 entries and 32 MiB per scene, plus a 96 MiB process pool shared with painter traces |
| Label traces | `scene["_label_trace_cache"]` | 8 entries and 4 MiB per scene |
| Polyhedron painter traces | `_background_dict_cache`, `_foreground_dict_cache` | 4 entries and 24 MiB per map, inside the same 96 MiB pool |
| Figure body | `ViewerBackend._figure_cache` | 16 entries and 128 MiB |
| Figure journal | `ViewerBackend._figure_broadcasts` | 32 envelopes and 64 MiB of figure bodies; the newest figure is kept |
| Base scene | `LoadedCrystal.scene_cache` | 8 entries and 64 MiB of geometry |
| Transformed scene | `LoadedCrystal._transformed_scene_cache` | 6 entries and 96 MiB of geometry |
| Fragment table | `LoadedCrystal.fragment_table_cache` | 8 entries and 32 MiB |
| Topology analysis memo | `LoadedCrystal._analyze_topology_cache` | 512 entries and 48 MiB |
| Topology geometry | `LoadedCrystal._topology_state_cache` | 8 entries (existing) |

`build_figure` attaches the mesh and label maps to the caller's scene
before its shallow copy, so label and axis toggles reuse one bounded map
instead of building a cache that dies with the temporary copy. Eviction
drops whole entries. It does not change the vertices of a style that is
still cached, and a cache miss rebuilds that same style.

Closing a scene drops figure-cache and journal bodies for that scene id.
When no open scene still displays the structure, the bundle's mesh maps,
transformed-scene cache, and topology trace caches are cleared too.
Replacing a scene's structure does the same for the structure that was
just detached. The loaded `bundle.scene` geometry itself stays, so opening
the structure again does not reparse the file.

### Callback Memo Caches

Current callback-local caches should disappear or be promoted to declared cache
selectors.  The module split fixed two stale-key regressions, but the pattern is
still migration debt:

- `app/callbacks_view.py::update_view._topo_cache_key` now includes
  `transforms`, `bond_groups`, and `cutoff`, but it is still an inline tuple
  owned by a callback rather than a named selector.
- `app/callbacks_state.py::refresh_fragment_options._cache` now includes
  `transforms_cache_key(transforms)`, but it is still callback-local state.

## Invalidations

| Invalidation | Clears / misses |
| --- | --- |
| `scene_geometry` | base scene, transform scene, topology, figure body, side panel |
| `transform_geometry` | transform scene, topology, figure body, side panel |
| `topology_geometry` | topology, figure body, side panel |
| `figure_body` | figure body only |
| `camera_layout` | camera overlay / uirevision compatibility; not mesh caches |
| `side_panel` | fragment options, summary, topology text |

The reducer emits invalidations.  Cache owners decide whether to clear, miss, or
reuse with overlay refresh.

This is the single picture the redesign is converging on: operations emit
tokens at the top; each token fans down into the caches it has to clear or
miss.  Anything not on the receiving end of an arrow must be left alone — that
is what stops a label-toggle from rebuilding the whole scene, and what stops a
color edit from re-running topology analysis.

```mermaid
flowchart TD
    invScene["scene_geometry"]
    invTrans["transform_geometry"]
    invTopo["topology_geometry"]
    invFig["figure_body"]
    invCam["camera_layout"]
    invSide["side_panel"]

    cacheBase["Base Scene Cache"]
    cacheTrans["Transform Scene Cache"]
    cacheTopo["Topology Caches<br/>(_topology_state_cache,<br/>_analyze_topology_cache)"]
    cacheFig["Figure Body Cache"]
    cacheCam["uirevision &<br/>compass / camera overlay"]
    cacheSide["Side panel<br/>(fragment options, summary, topology text)"]

    invScene --> cacheBase
    invScene --> cacheTrans
    invScene --> cacheTopo
    invScene --> cacheFig
    invScene --> cacheSide
    invTrans --> cacheTrans
    invTrans --> cacheTopo
    invTrans --> cacheFig
    invTrans --> cacheSide
    invTopo --> cacheTopo
    invTopo --> cacheFig
    invTopo --> cacheSide
    invFig --> cacheFig
    invCam --> cacheCam
    invSide --> cacheSide
```

## Reverse Hooks

- A transform change must change the fragment-options selector key.
- A bond-group change must either update side-panel summaries or be proven
  irrelevant by a test.
- A polyhedron color-only edit must reuse topology geometry but repaint figure
  traces.

## Invariants

- Cache keys are computed by named selector functions, not inline callback
  tuples.
- A cache value never stores mutable state that callers can modify as a side
  effect.
- Camera is excluded from figure-body cache only after camera compatibility has
  been checked.
- Callback-local memoization cannot omit geometry-changing fields.

