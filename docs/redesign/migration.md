# Migration Roadmap

The state-machine rewrite should not land as one giant PR.  The current app is
large and user-facing; each phase below has a narrow invariant gate.

## Phase Progression

```mermaid
flowchart LR
    p1["Phase 1<br/>Geometry + Viewport"]:::done
    p15["Phase 1.5<br/>Scene-tabs single writer"]:::done
    p2["Phase 2<br/>Selectors"]:::pending
    p3["Phase 3<br/>Operation Reducer"]:::pending
    p4["Phase 4<br/>Cache Centralization"]:::pending
    p5["Phase 5<br/>Rendering Resolver"]:::pending
    p6["Phase 6<br/>Callback Simplification"]:::done
    p1 -- "viewport corner gate" --> p15
    p15 -- "one tab DOM writer" --> p2
    p2 -- "selector cache-key gate" --> p3
    p3 -- "one agent-state writer" --> p4
    p4 -- "named selector keys" --> p5
    p5 -- "one viewport writer" --> p6
    classDef done fill:#d4edda,stroke:#155724,color:#155724;
    classDef pending fill:#f8f9fa,stroke:#6c757d,color:#212529;
```

Green phases (1, 1.5, and the structural half of 6) have landed in this
branch. Each arrow label is the invariant test that must pass before the next
phase starts; if a Phase 3 PR is reviewed and the Phase 2 selectors are not yet
in place, the reviewer should push back rather than thread operation handling
through ad hoc selectors.

## Phase 1: Geometry And Viewport Baseline

Status: started by the viewport/camera fixes in this branch.

Scope:

- keep `docs/derivations/` current;
- include visible unit-cell corners in viewport ranges;
- clear incompatible stored camera on display-signature changes;
- add focused regression tests.

Gate:

- ASU/formula unit-cell boxes are not clipped;
- display-mode changes no longer reuse stale cube-space camera;
- topology `extra_overlays` still do not expand non-unit-cell viewports.

## Phase 1.5: Scene-Tabs Single Writer

Status: implemented in this branch as an accelerated fix for upload/tab
state corruption.

Scope:

- add `scene-event-store` as the browser event edge for scene CRUD;
- make `manage_scene_tabs_dom` the only writer for `scene-tabs.children` and
  `scene-tabs.value`;
- make duplicate / rename / close / close-others callbacks mutate the backend
  `SceneStore` and emit an event instead of patching tab DOM directly;
- remove scene-tab DOM outputs from `sync_agent_state`;
- keep native upload polling, but stop JavaScript from calling
  `set_props("scene-tabs", ...)`.

Gate:

- callback-map tests prove `scene-tabs.children` and `scene-tabs.value` each
  have one writer;
- upload append, close-others, and per-row close paths update
  `backend.scene_options()`;
- `sync_agent_state` no longer outputs either scene-tab prop.

## Phase 2: State Selectors

Scope:

- introduce selector helpers for stored, derived, and ephemeral state;
- move `show_hydrogen`, `show_unit_cell`, labels, axes, and viewport signature
  into selectors;
- keep existing callback wiring but make callbacks call selectors instead of
  deriving booleans ad hoc.

Gate:

- toggling labels/axes does not change scene-cache keys;
- toggling hydrogens does change scene-cache keys;
- selector tests cover all `display_options` tokens.

## Phase 3: Operation Reducer

Scope:

- add typed operation objects or dicts;
- implement reducer branches from `operations.md`;
- route REST, WebSocket, and Dash state writes through the reducer while
  keeping old endpoint shapes.

Gate:

- `agent-state-store.data` has one persisted-state writer;
- tests cover display, transform, group, topology, camera, and scene-tab
  operations;
- every operation returns invalidations.

## Phase 4: Cache Key Centralization

Scope:

- replace callback-local key tuples with named selector functions;
- fix fragment-options and side-panel stale keys;
- make figure cache depend on resolved render state and compatible camera
  policy.

Gate:

- transform edits refresh fragment options and side panels;
- bond-group edits either update summaries or are proven irrelevant;
- color-only polyhedron edits reuse topology geometry.

## Phase 5: Rendering Resolver

Scope:

- introduce a viewport resolver that returns ranges, aspect, cube scale, and
  signature;
- make figure layout, compass projection, and camera remapping consume that
  resolver;
- delete or delegate duplicate viewport helpers outside `render/viewport.py`.

Gate:

- only one main-viewer path writes `aspectmode` / `aspectratio`;
- compass tests prove shared cube-scale behavior;
- camera remapping can replace the current reset policy if desired.

## Phase 6: Callback Simplification

Status: structural split implemented in this branch; reducer ownership is still
pending.

Scope:

- remove `allow_duplicate=True` state writers where the reducer dispatcher has
  taken ownership;
- keep the split Dash implementation in `app/*`, `app/callbacks_*`, and
  `app/backend*` modules rather than regrowing `app/dash_impl.py`;
- keep public REST and stable Dash IDs unchanged unless a version bump is
  planned.

Gate:

- callback graph has one writer per persisted store;
- API compatibility tests pass;
- no public `agents/*.md` contract is broken without an explicit update.

## PR Size Rule

Each phase should be small enough to review by invariant:

- one conceptual owner changed;
- targeted tests added before broad tests;
- no opportunistic style cleanup outside touched modules;
- no public API changes without matching `agents/*.md` updates.

## Invariants Across All Phases

- Do not reimplement MolCrysKit chemistry in MatterVis.
- Do not mutate global palettes or module-level render state.
- Do not collapse display mode, transform mode, and camera mode into one flag.
- Do not preserve compatibility with broken in-branch behavior when a documented
  invariant says it is wrong.

