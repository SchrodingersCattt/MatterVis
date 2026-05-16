# State Model

MatterVis currently treats the scene state dict as a catch-all object.  It
stores user intent, derived style, cache hints, camera transport, legacy aliases,
and REST shorthands in one namespace.  The target design splits those concerns.

## Target Categories

### Stored State

Stored state is user intent that must survive tab switches, presets, and REST
round trips.

| Key | Target owner | Notes |
| --- | --- | --- |
| `scene_id`, `scene_label` | scene store | Identity and UI label. |
| `structure` | scene store | Catalog/upload structure selected by the scene. |
| `display_mode` | display reducer | `formula_unit`, `unit_cell`, `asymmetric_unit`, `cluster`. |
| `display_options` | display reducer | Checkbox tokens; should eventually become typed booleans. |
| `atom_scale`, `bond_radius`, `minor_opacity`, `axis_scale` | style reducer | Numeric render controls. |
| `material`, `style`, `disorder`, `ortep_mode`, `label_mode` | style reducer | Independent style axes. |
| `projection` | camera reducer | Camera projection intent independent of eye vector. |
| `cutoff` | topology reducer | Candidate shell search radius. |
| `topology_site_index` | topology reducer | Selection into the current fragment list; nullable. |
| `polyhedron_specs` | polyhedra reducer | Named coordination overlays. |
| `atom_groups` | atom-group reducer | Per-atom render overrides. |
| `bond_groups` | bond-group reducer | Per-bond render overrides. |
| `transforms` | transform reducer | Ordered transform pipeline. |
| `camera` | camera reducer | Optional saved camera for a compatible viewport signature. |
| `camera_revision` | camera reducer | Revision counter for accepting layout-supplied camera changes. |

### Derived State

Derived state must not be persisted as independent truth.

| Value | Derivation |
| --- | --- |
| `show_hydrogen` | `"hydrogens" in display_options`. |
| `show_unit_cell` | `"unit_cell_box" in display_options`. |
| `show_axes` | `"axes" in display_options` or axis-key style. |
| `show_labels` | `"labels" in display_options` plus label mode. |
| `monochrome` | Atom-group rule, not a display option. |
| `fast_rendering` | Derived from explicit setting plus `material == "flat"` and atom-count threshold. |
| `uirevision` | Function of scene name, `camera_revision`, and viewport signature. |
| `fragment_options` | Derived from resolved scene fragment table. |
| `topology_payload` | Derived from scene geometry, selected site, cutoff, and specs. |
| `figure` | Derived from render model, topology payload, style, and compatible camera. |

### Ephemeral State

Ephemeral state may exist in the browser or request scope but should not be
treated as durable scene intent.

| Value | Scope |
| --- | --- |
| `version` | Backend polling / cache-busting metadata. |
| `server_started_at` | Backend metadata. |
| `camera-state-store` | Browser-owned live camera during drags. |
| `pending_state` | Backend-to-browser synchronization queue. |
| Click hover payloads | Callback event scope. |
| Right-click menu target | UI event scope. |
| `topology_fragment_type`, `topology_show_all_sites` | Legacy request aliases normalized into stored keys. |
| `supercell` | REST shorthand normalized into a `repeat` transform. |

### Taxonomy at a Glance

The three categories above should partition every key the viewer carries.
Stored values are the only durable truth; derived values fall out of stored
values via selectors; ephemeral values enter from out-of-band channels (REST
aliases, browser events, polling metadata) and either get migrated into stored
form or vanish at the end of the request scope.

```mermaid
flowchart LR
    subgraph Stored["Stored (user intent)"]
        sIdentity["scene_id / scene_label / structure"]
        sDisplay["display_mode / display_options"]
        sStyle["material / style / disorder<br/>atom_scale / bond_radius / minor_opacity"]
        sLists["transforms / atom_groups / bond_groups / polyhedron_specs"]
        sTopo["cutoff / topology_site_index / topology_enabled"]
        sCam["projection / camera / camera_revision"]
    end
    subgraph Derived["Derived (recomputed)"]
        dFlags["show_hydrogen / show_unit_cell<br/>show_axes / show_labels"]
        dRender["monochrome / fast_rendering"]
        dPanel["fragment_options / topology_payload"]
        dFig["uirevision / figure"]
    end
    subgraph Ephemeral["Ephemeral (out-of-band)"]
        eMeta["version / server_started_at"]
        eBrowser["camera-state-store / pending_state"]
        eEvent["click / hover / right-click target"]
        eAlias["topology_fragment_type / topology_show_all_sites / supercell"]
    end
    Stored -->|selector| Derived
    Derived -->|cache key| dFig
    eAlias -.->|migrated at API boundary| Stored
    eBrowser -.->|overlay applied post-cache| dFig
    eMeta -.->|excluded from figure cache key| dFig
```

## Current Gaps

`normalize_state` currently performs too many roles:

- scene switching and structure defaulting;
- legacy request migration;
- style normalization;
- topology selection resets;
- supercell shorthand expansion;
- monochrome-to-atom-group migration;
- camera compatibility handling;
- projection synchronization.

The diagram below shows the same eight roles fanning out of a single function.
Every arrow is a place where the patch shape implicitly mutates an unrelated
stored key; the reducer redesign aims to make each branch its own named
operation with declared invalidations.

```mermaid
flowchart LR
    patch["Incoming patch dict"] --> norm["normalize_state(...)"]
    norm --> r1["Scene switch & structure default"]
    norm --> r2["Legacy alias migration<br/>(topology_fragment_type,<br/>topology_show_all_sites)"]
    norm --> r3["Style normalization<br/>(material / style / disorder / scales)"]
    norm --> r4["Topology selection reset<br/>(display_mode change → site_index=None)"]
    norm --> r5["Supercell shorthand<br/>(replaces existing repeat transforms)"]
    norm --> r6["Monochrome flag<br/>→ appended atom_group rule"]
    norm --> r7["Camera compatibility<br/>+ camera_revision bump"]
    norm --> r8["Projection sync<br/>(top-level ↔ camera.projection.type)"]
    r1 --> stored["Stored Scene State"]
    r2 --> stored
    r3 --> stored
    r4 --> stored
    r5 --> stored
    r6 --> stored
    r7 --> stored
    r8 --> stored
```

The target reducer should split those into named operations with explicit
invalidations.

## Target Shape

```mermaid
flowchart TD
    stored["Stored Scene State"] --> selectors["Selectors"]
    selectors --> derived["Derived View State"]
    derived --> caches["Cache Keys"]
    ephemeral["Ephemeral UI State"] --> operation["Operation Payload"]
    operation --> reducer["Reducer"]
    reducer --> stored
```

Selectors compute derived values without mutating stored state.  Operations
mutate only stored state.  Caches consume derived keys but do not become state.

## Reverse Hooks

- A test that toggles labels must show no change to scene geometry cache keys.
- A test that changes `display_mode` must invalidate camera compatibility and
  reset or remap the stored camera.
- A test that changes `material` to `flat` must not persist a contradictory
  independent `fast_rendering=False` value.

## Invariants

- No new stored key may be added without declaring its owner and invalidations.
- Legacy aliases are accepted only at API boundaries and are never persisted.
- Derived values are recomputed from selectors; they are not patched by
  callbacks.
- Camera is stored only when compatible with the current viewport signature.

