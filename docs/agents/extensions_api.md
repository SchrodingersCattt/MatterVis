# Optional in-process frontend extensions

MatterVis retains its native Dash viewer and Textual viewer. Plugins attach
optional right-side panels to those hosts; they do not construct another
`ViewerBackend` or replace the native renderer, controls, graph IDs or bindings.
Importing `mat_viewer.extensions` uses only the standard library.

## Entry points

- Web: `from mat_viewer.app import create_app` (lazy facade), or
  `from mat_viewer.app.factory import create_app` (implementation).
  Signature: `create_app(preset_path=DEFAULT_PRESET_PATH, names=None,
  root_dir=None, cif_paths=None, input_path=None, input_format=None,
  type_map=None, frame=0, property_data=None, atom_property_color=None,
  *, extensions=()) -> Dash`. Existing positional parameters are unchanged.
- TUI: `from mat_viewer.tui.app import CrystalTUI`.
  Signature: `CrystalTUI(crystal, *, mono=False, initial_view="auto",
  camera=None, show_bonds=True, show_cell=True, label_mode="auto",
  show_minor=False, compact=False, initial_level="atom", extensions=())`.

Pass a tuple of plugin instances at startup. Each instance belongs to one host;
do not share resource-owning instances across apps. Names must be unique and
match `[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+`, for example `example.chat`.
Invalid or duplicate names raise `ValueError` before host construction or hooks;
ownership has not transferred, so callers close those rejected instances.

## Plugin contract

Subclass `mat_viewer.extensions.Extension` and set `name: str`.
All hooks default to no-ops (`None`):

| Hook | Meaning |
|---|---|
| `build_web_panel(context)` | Return a Dash component or `None`; called once during creation |
| `register_web(app, context) -> None` | Register callbacks/routes once, after native layout and callbacks, before serving |
| `build_tui_panel(context)` | Return a Textual `Widget` or `None`; called once during composition |
| `on_tui_mount(app, context) -> None` | Initialize UI-side polling after native viewer and plugin widgets mount |
| `on_tui_unmount(app, context) -> None` | Stop UI-side polling during teardown |
| `close() -> None` | Release workers/resources; must not call UI methods |

Use frontend-specific imports inside the matching implementation:
`from dash import html, dcc, Input, Output` for Web, and
`from textual.widget import Widget` (or concrete widgets) for TUI. The shared
extension module does not import either optional frontend or any model SDK.
The hooks are synchronous and must return promptly. Plugins own asynchronous
work and future polling; no background work is executed for them by this API.

Panels appear only when at least one plugin returns a panel. The Web container
`mv-extension-panels` is the optional third column, to the right of the native
viewer. Without plugin panels, the Web layout has only a left tool sidebar and
the center plot. Multiple plugin panels share that one extension container.
The TUI container of the same ID is appended inside the original `body` after
canvas and inspector (the native narrow-screen vertical layout still applies).
Use plugin-prefixed component/widget IDs; do not reuse host IDs. With no panels,
no extra layout container is emitted. TUI text input in plugin panels does not
flow through the native direct movement-key handler.

### Web tool placement

The left sidebar keeps scene navigation and CIF upload above horizontal
**Display / Analysis / Operations** tabs. All native controls are mounted at
startup, including inactive panes; switching tabs changes only CSS visibility,
not callback registration, scene state, or the renderer. Native advanced
sections remain collapsible within their panes.

Click or use Enter/Space to select a tab; Left/Right arrows and Home/End move
between tabs. Selected and pressed ARIA states follow the active tab. Clicking
the active tab leaves it open. Selection is browser-page-local and resets to
Display on refresh; it is not persisted in scenes, presets, or browser storage.

The Analysis pane shows a distance histogram (resized when the pane
becomes visible), plain-text shell results, and a short atom list. The
canvas has a status bar (`mv-status-bar`) for the structure label, scope,
and atom/bond counts, plus a working indicator for user-driven updates.
The server log (`perf-log-panel`) filters by level and collapses
consecutive repeats. Measurement tools (distance, angle, dihedral) stay
on the terminal viewer.

Existing `analysis-panel-toggle` and `operation-panel-toggle` IDs are retained;
`display-panel-toggle` is added. The legacy `right-panel` ID now identifies the
analysis/operations container **inside** `left-panel`, not a root column.
Only the left sidebar has a splitter; `right-splitter` is removed. The native
server-log overlay lives within `center-panel`, with its width constrained to
the viewer so it cannot cover extension inputs. Plugins do not need to reserve
space for that overlay.

## Context and snapshots

Both hosts expose `app.extension_context` and pass the same context to every
hook. `ExtensionContext.frontend` is `"web"` or `"tui"`.
`ExtensionContext.viewer` is the **existing** `ViewerBackend` for Web and the
**existing** `CrystalTUI` app for TUI. On TUI, `viewer.crystal` and
`viewer.controller` provide in-process access to the current IR and controller.
Web callers may use existing backend APIs on their supported execution paths.
The context adds no chemistry mutation API and no source-object serialization.

`context.snapshot() -> dict` is thread-safe and returns detached metadata:

| Key | Web | TUI |
|---|---|---|
| `frontend` | `"web"` | `"tui"` |
| `structure_id` | Current backend state's `structure` | Opaque owned source key |
| `source_path` | `None` (not in locked backend state) | Current IR's `source_path`, or `None` |
| `scene_id` | Current backend state's `scene_id`, or `None` | Stable native scene ID |
| `scene_generation` | Host scene generation (changes on Undo) | `1` (closed IDs are never reused) |
| `view_revision` | Actual backend `render_revision` | Last UI-applied observation revision, initially `None` |
| `selection` | Backend state's selection, or `None` | Last UI-applied selection dict, initially `None` |

Web snapshots use the existing locked `get_state()`; they describe shared server
state, not a particular browser's uncommitted local interactions. TUI publishes
metadata on the event-loop thread whenever an observation is applied; worker
reads never inspect mutable widgets/controllers. A title is not a stable ID;
unknown identities, paths and revisions are intentionally `None`. Revision
semantics are frontend-specific, not a new chemistry version counter.

Live source/viewer references stay in-process. Detached snapshots and the `structures` service are safe to use from workers. Run all host UI method calls on the
frontend execution thread: Textual's event loop (use `app.call_from_thread`
from workers), or Dash's framework-managed callbacks/creation path. Dash does
not have a single global UI thread; do not mutate layouts from workers or
register callbacks after serving begins. This contract adds no synchronization
for arbitrary live backend/source access.

## Shutdown and failures

Both hosts provide `app.close_extensions()`, an idempotent resource cleanup
method. All plugins are closed in reverse order, including not-yet-started
plugins after a startup hook failure. Cleanup exceptions are logged and do not
prevent other plugins from closing. `close()` can run on the caller's shutdown
thread, so it must not access UI methods.

Web registers cleanup with `atexit` as a fallback; production hosts should call
`app.close_extensions()` explicitly in their shutdown `finally`, then
`app.crystal_backend.close()`. Do not use Flask request teardown as application
shutdown. Startup failure closes plugins and the constructed backend.

TUI unmount invokes lifecycle hooks in reverse mount order, then closes resources.
A failing mount hook receives an unmount attempt too. Build-hook failures close
resources immediately. If a host is constructed but never run, its owner must
call `close_extensions()`. Embedders should also use a shutdown `finally` for
failures outside plugin hooks. Resources released by `close()` must tolerate
partial initialization. Explicit close does not trigger UI unmount hooks.

Registration is startup-only: no hot callback unloading, widget replacement,
plugin reload, or post-start layout monkeypatching. `register_web` has no consumed
return value; a plugin retains its bound context on its instance if needed.

## Regression tests

- `tests/test_extensions.py`: names, default hooks, detached worker snapshots,
  concurrent idempotent close and cleanup-failure isolation.
- `tests/app/test_web_extensions.py`: native layout, panel-less plugins,
  build/register once, shared backend/context, callback registration, duplicate
  rejection and startup cleanup.
- `tests/app/test_workbench_layout.py`: actual root columns, mounted panes,
  unique component IDs, fixed callback dependencies, shared scene/upload tools,
  viewer-contained diagnostics and unchanged extension mounting.
- `tests/tui/test_tui_extensions.py`: async `run_test` composition/lifecycle,
  worker snapshots, plugin input, duplicate rejection and build-failure cleanup.

## Native structure writes (v1)

`context.structures` is a `StructureWriter` with schema
`mattervis.structures/v1`, available on both native hosts. Its public types
`SceneHandle` and `SceneEvent` are exported from `mat_viewer.extensions`.

```python
# From a worker (never block a frontend callback on .result()):
writer = context.structures
scene_id = writer.register("result.cif", label="Optimized").result()
handle = writer.handle(scene_id)  # scene_id + generation
writer.reload(handle, cif_bytes, format="cif").result()
# TUI:
writer.focus(handle).result()
# Web: capture State("extension-browser-target", "data") in the initiating
# browser callback, then carry that exact token into its worker:
writer.focus(handle, target=browser_target).result()
```

- `register(source: str | Path | bytes, *, format=None, label=None) -> Future[str]`
  creates a scene without changing the current browser/terminal focus.
- `reload(scene_id: str | SceneHandle, source, *, format=None) -> Future[str]`
  replaces one native source, returning the same scene ID. Each successful
  write receives a unique internal source/bundle key; duplicate sibling scenes
  keep their old bundle. Scientific reload resets selections, transforms,
  topology controls and per-source styles. Camera is preserved only when cell,
  displayed coordinates and elements are identical and no Web transform or
  display-mode change is being reset; otherwise it refits.
- `focus(scene_id: str | SceneHandle, *, target=None) -> Future[None]`, also
  spelled `select`, switches a **scene**, not an atom selection. Web completion
  means the focus request was queued for the specified page, not acknowledged
  by its browser. TUI completion means the native canvas/controller switched.
- `handle(scene_id) -> SceneHandle` pins the current generation. String IDs are
  resolved when the call is submitted; handles remain stale after close/Undo.
  Native Web Undo may restore the same ID with a new generation. All pending
  writes revalidate at commit, so neither close nor Undo revives old commands.
- `subscribe(callback) -> unsubscribe` delivers frozen `SceneEvent` values:
  `kind` (`registered`, `reloaded`, `focused`, `closed`), `scene` (handle),
  optional `source_id` (on writes), schema `mattervis.scene-event/v1`.
  Events follow successful commits; close events include the removed generation.
  Subscribers run on the committing host thread, must be short, and must not
  wait on another write. Exceptions are logged without undoing the commit.
  This is a lifecycle seam, not revision history or a replayable event log.

V1 deliberately accepts only CIF. Bytes require `format="cif"`; paths infer
`.cif` or accept that explicit format. Empty input, invalid labels, unsupported
formats and unknown scene IDs are rejected. Submission validation raises
synchronously; parse/commit failures are delivered through the future. Path
bytes are copied before submission returns, so editing or deleting the caller's
file cannot affect a queued write. Candidates are parsed before host mutation;
failed candidates leave the old scene intact and their files are removed.
Successful private files remain host-owned until shutdown, including through
close/Undo; callers must not edit them. These are in-process sources and are not
restored across app restarts. Shutdown rejects new writes and outstanding
commits, then removes files after the worker drains.

Web scenes are shared at **app** scope. The random `extension-browser-target`
store value is a per-page routing token, not a login/session authorization
boundary. Pass the initiating page's token explicitly; missing/unknown tokens
fail. No broadcast focus fallback exists. Registration/reload sends a scene
refresh event to each page while preserving its selected scene. Focus requests
coalesce per target and pass through the existing single tab DOM dispatcher;
a closed generation is discarded at delivery. No callback or layout is added
or replaced after serving starts.

CIF preparation uses the service's serial worker. Web commit and native scene
lifecycle mutations share the backend lock; Dash has no global UI thread. TUI
requires a mounted app and schedules commits through `call_from_thread` on the
Textual loop. Its `TerminalScenes` host owns real native controllers and a scene
selector, retaining the existing canvas. Unused services allocate no source
storage and start no write worker. Optional frontend imports stay in adapters.

The new source key and incremented `target_revision` are the integration seam
for selection guards in #216; this branch does not import or modify that PR.
Downstream #218 should invalidate mappings on `closed` and never infer that
Undo resurrects a scientific binding. #219 can consume this same API without
introducing another canvas.
