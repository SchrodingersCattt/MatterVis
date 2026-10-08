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
| `structure_id` | Current backend state's `structure` | `None` (IR has no stable structure ID) |
| `source_path` | `None` (not in locked backend state) | Current IR's `source_path`, or `None` |
| `scene_id` | Current backend state's `scene_id`, or `None` | `None` |
| `view_revision` | Actual backend `render_revision` | Last UI-applied observation revision, initially `None` |
| `selection` | Backend state's selection, or `None` | Last UI-applied selection dict, initially `None` |

Web snapshots use the existing locked `get_state()`; they describe shared server
state, not a particular browser's uncommitted local interactions. TUI publishes
metadata on the event-loop thread whenever an observation is applied; worker
reads never inspect mutable widgets/controllers. A title is not a stable ID;
unknown identities, paths and revisions are intentionally `None`. Revision
semantics are frontend-specific, not a new chemistry version counter.

Live source/viewer references stay in-process. Only detached snapshots are
safe to read directly from worker threads. Run all host UI method calls on the
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