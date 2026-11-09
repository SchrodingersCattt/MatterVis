# Scene delivery races

Scene, camera, and figure publication are per scene. A packet for scene A
must not change scene B, and a stale revision must not replace a newer one.

## Publication contract

- A committed change schedules a complete frame only when that change needs
  one. Camera, overlay, and ordinary display patches stay on the view-update
  journal. Duplicating a scene is a complete frame for the copy: the catalog
  can rename the tab before the canvas title exists, and the title arrives
  only with the figure.
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
- The browser relayout gate drops a camera gesture whose scene is not the
  mounted canvas. It restores the mounted scene's own saved camera only when
  that camera belongs to the mounted scene. A settled gesture does not copy
  the mounted scene's generation onto a gesture that still names another scene.
  The pointer-up relayout keeps the revision from gesture start. A later
  camera relayout on that same scene reads the live camera clock, including
  inside the settle window, so the server can accept a refit of an offscreen
  drag. That clock is `scene_id:scene_generation`. A typed camera envelope
  carries `scene_generation` beside `versions`, not inside it. The apply
  path reads that sibling. Reading only `versions` stores the new revision
  on an empty generation, the next fence sends the figure's older revision,
  and the server rejects the offscreen repair.
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
