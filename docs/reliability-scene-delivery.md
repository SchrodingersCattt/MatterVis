# Scene delivery races

Scene, camera, and figure publication are per scene. A packet for scene A
must not change scene B, and a stale revision must not replace a newer one.

## Publication contract

- A committed change schedules a complete frame only when that change needs
  one. Camera, overlay, and ordinary display patches stay on the view-update
  journal. Duplicating a scene is a complete frame for the copy: the catalog
  can rename the tab before the canvas title exists, and the title arrives
  only with the figure.
- A committed complete frame whose scene, generation, and revision are
  current is applied whether or not it contains atom coordinates. An empty
  scene, deletion of the last atom, hiding every atom, a publication image,
  a cell, and an overlay are final intents. Hiding atoms omits their traces.
  A selection trace may have an empty `x`, and a formula-unit scene may have
  no cell mesh. None of those shapes means the frame is missing. Trace count
  and non-empty coordinates are not the version check.
- `layout.meta.mattervis_pending` is a loading placeholder. It is not a
  committed empty scene. It does not replace a picture already committed for
  the selected scene, and it does not drop a queued committed frame. The
  status line may show atom and bond counts before the first frame for that
  scene is mounted; that interval is loading, and it ends when the committed
  frame is applied.
- A full figure with no scene identity and no traces does not replace a
  canvas that already belongs to a scene. That payload is not a committed
  empty scene. A full figure with no scene identity is also dropped when the
  selected scene is no longer the scene on the canvas.
- Before `Plotly.react` is issued, a frame whose scene, generation, or
  revision is no longer current is not issued. A call that has already been
  issued is not cancelled and can still write. A newer committed frame
  queued behind it is applied after that write, and that newer frame is the
  picture that remains, including when it has no traces.
- The browser's delivered-frame identity is the server session, scene id,
  scene generation, render revision, camera revision, and `uirevision`.
  `uirevision` itself is scene id, structure name, camera revision, and
  viewport signature. Closing a scene and undoing that close keeps the scene
  id, sets the render revision back to zero, and assigns a new generation.
  The next geometry or display change can make the render revision 1 again,
  and the camera revision and `uirevision` can match the pre-close frame.
  That restored frame is still delivered, empty or not. A repeated identity
  that also repeats the generation is the duplicate that is not rebuilt.
- A complete frame that has not started is dropped when a newer committed
  frame for the same scene is queued. Newer is a higher scene generation, or
  the same generation and a higher render revision, or the same generation
  and revision and a higher camera revision. An empty payload does not lose
  that comparison to an older payload that still has coordinates.
- Worker identity is `(scene_generation, render_revision, camera_revision)`.
  The finished build is published when that identity is still current. A
  strictly newer identity is requeued when the transition still needs a
  complete frame, or when only the camera revision advanced. Display and
  overlay edits bump `render_revision` and are not requeued. An older
  identity is dropped.
- `broadcast_figure` ignores a body whose scene cache key or render revision
  is no longer current. `latest_figure_broadcast` does not return that body.
- `layout.scene.uirevision` is
  `{scene_id}__{structure name}__{camera_revision}__{viewport signature}`.
  Plotly keeps a WebGL camera only while that token matches.
- An ordinary drag, pan, or zoom writes `state["camera"]` and does not
  advance `camera_revision`. `latest_figure_broadcast` still returns the
  figure that was published. It does not rewrite that snapshot from the
  live camera. The browser keeps the camera from `camera-state-store`
  for that scene, revision, and generation, and puts it on the canvas
  after a tab return or a stale same-revision frame. A store whose
  revision is older than the mounted frame is ignored. Reset, align,
  projection, and a refit that advances the revision still win. A
  camera that leaves the scene, including a unit-cell box with a corner
  outside the frustum, is still replaced by `refit_scene_camera` before
  that pose is what the store paints. Every writer of
  `camera-state-store` uses `allow_duplicate`.
- A complete frame whose scene id differs from the mounted scene is followed
  by a relayout of that figure's camera, axis ranges, aspect mode, aspect
  ratio, and `uirevision`. `Plotly.react` writes those values onto the
  layout, and the gl3d scene keeps drawing in the previous axis box until a
  later relayout rebuilds it. A width or height change is one such rebuild,
  so the new geometry can sit in the previous box until the plot is resized.
  The follow-up relayout is not a pointer gesture and is not stored as a
  drag. A later frame for the same scene still keeps the live camera when
  its `uirevision` matches. The projection control shows the selected
  scene's stored projection; the canvas uses the camera on that scene's
  figure. A failed scene-box relayout rejects the frame and does not mark
  that frame delivered.
- The browser relayout gate drops a camera gesture whose scene is not the
  mounted canvas. It restores the mounted scene's own saved camera only when
  that camera belongs to the mounted scene. A settled gesture does not copy
  the mounted scene's generation onto a gesture that still names another scene.
  The pointer-up relayout keeps the revision from gesture start. A later
  camera relayout on that same scene reads the live camera clock, including
  inside the settle window, so the server can accept a refit of an offscreen
  drag. That clock is `scene_id:scene_generation` plus the server session
  (`server_started_at`) when both the transient revision and the mounted
  figure have one. A typed camera envelope carries `scene_generation` beside
  `versions`, not inside it. The apply path reads that sibling. Reading only
  `versions` stores the new revision on an empty generation, the next fence
  sends the figure's older revision, and the server rejects the offscreen
  repair. A later relayout reads the transient revision only when its scope
  and session match the mounted figure. A revision left by another scene, or
  by a restarted session that reused the generation number, is not that clock.
- A width/height relayout is a full gl3d replot. During an in-progress drag
  it puts the live drag camera back only when the gesture's scene id and
  scene generation still match the mounted figure, and, when both the gesture
  and the figure have a server session, those sessions match. A remount that
  reuses the scene id with a new generation, or a restarted server, does not
  receive the pre-remount drag eye. A camera command that arrives during that
  replot still wins. A frame or internal replot that finishes while that
  drag is still down puts that gesture's sampled eye back when the
  generation and session still match. The sample is dropped when the
  gesture is stamped or released, so a later press cannot restore the
  previous orbit before it samples its own eye. A size replot does not
  put back a captured pose once a newer camera revision is already
  current, even when that command arrived before the replot. After the
  gesture ends, only a commanded camera is restored.
- `update_view` classifies a callback against the previous snapshot of the
  same scene. That snapshot is process-wide and is not a substitute for the
  requesting browser's mounted figure. Camera, overlay, and ordinary display
  deltas skip a new figure only when `current_figure` is already stamped
  with that scene. A browser whose mounted figure still belongs to another
  scene receives the selected scene's journal frame, including its title,
  render revision, camera revision, and projection. The first look at a
  scene is still a complete frame.
- Sidebar capture is per browser. The last writer remains `filled["id"]` for
  unfenced callers. `filled["by_client"]` is the guard used when the request
  carries a client id.
- A projection store whose scene is not the selected tab does not change the
  radio and does not write either scene's projection. That fence was already
  the server behavior; the browser harness that failed to observe the delayed
  callback is covered separately.
- If the selected scene's parked frame is still unapplied after the existing
  retry budget, the render status reports `Scene frame was not applied` and
  the parked frame is dropped. The budget does not grow. A frame parked for
  a scene that is not selected is kept when that budget ends, so a later
  selection can still paint it.
- Duplicating a scene publishes the catalog with `focus: false`. Other open
  pages keep their selected tab. The page that clicked Duplicate selects
  that catalog's `active_id` and flushes the copy's parked frame. The
  catalog flag is not turned on, so the selection stays on that page.

## Root causes

| Symptom | Cause |
| --- | --- |
| Duplicate canvas title never settles | The copy's figure completed, but the `focus: false` catalog replaced the tab event before the duplicating page selected the copy. That page stayed on the source scene, the copy frame was parked, and the retry budget then dropped it. The title stayed on the source. The duplicating page now selects the catalog `active_id` locally; other pages still keep their tab. |
| Drag on one scene reframes the next scene | The relayout wrapper applied the gesture whenever the saved camera owner was not the mounted scene. `uirevision` omitted the scene id, so a duplicate reused the source scene's WebGL camera. A settled gesture could also copy the new canvas generation onto the old gesture. |
| Two browsers collapse onto one scene's controls | The filled-scene guard was one process-wide id. The last tab claim decided which browser's sidebar capture was allowed to write. |
| Delayed view callback rebuilds the wrong scene | `update_view` kept one previous snapshot. A callback for scene A that arrived after scene B was classified as a scene change. The first look at a scene stays a complete frame; only later callbacks use that scene's own snapshot. |
| Selected tab and the live canvas diverge after another browser visited the scene | The per-scene snapshot is process-wide. A camera or display delta skipped the frame without reading the requesting browser's mounted `current_figure`. |
| In-flight build discarded after a complete-frame edit that never scheduled | Recovery requeued only a camera revision. A newer geometry identity was cancelled. Display and overlay clocks still do not requeue. |
| Projection radio and a delayed store | The server already ignores a store for a different scene. The failure to observe that callback was harness timing, not this publication path. |

## Local repeat evidence

`tests/app/test_scene_delivery_races.py` and
`tests/app/test_plotly_relayout_fence_js.py` hold the worker or the Plotly
relayout call and reorder the scene packets. They do not sleep.

Twenty consecutive runs of those two files completed with no failures,
including after frame supply began using the caller's mounted figure:

```
python3 -m pytest tests/app/test_scene_delivery_races.py tests/app/test_plotly_relayout_fence_js.py -q --tb=line
```

Each run builds a fresh backend. The two-browser Plotly check is
`test_second_client_plots_the_scene_after_the_other_clients_camera_change`.
It passed once locally on an isolated server. Five fresh CI browser workers
are not claimed from this local repeat, and this pull request does not treat
the Camera WebGL job as passed until that job is green.
