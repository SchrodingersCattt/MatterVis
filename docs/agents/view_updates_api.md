# View update protocol

MatterVis uses one delivery envelope for Dash, HTTP fallback, and the figure
WebSocket:

```json
{
  "type": "view_update",
  "update_kind": "overlay|display|camera|geometry|analysis",
  "operation_id": "display",
  "scene_id": "...",
  "geometry_version": 3,
  "display_version": 4,
  "camera_version": 2,
  "payload": {}
}
```

`geometry_version` scopes every local patch. A display, overlay, or analysis
patch is rejected when it targets another geometry version; a camera command
is checked against `camera_version`. Complete figures carry the same fields in
`layout.meta.mattervis_render`. The browser submits these messages through
`view_updates.js`; no callback writes a competing full figure for a camera or
overlay action.

Typed envelopes default `operation_id` to their update kind. This keeps
different kinds at the same versions distinct while repeated confirmations
of the same kind deduplicate. A caller-supplied `operation_id` in the payload
overrides that default.

Camera broadcasts keep the update kind confirmed by the state transaction;
adding wire metadata must not reclassify them as display patches. Partial
browser camera captures merge with the backend camera at the captured
revision, including dotted projection updates, so a stale Dash store cannot
restore the previous projection.

A state transaction that changes both projection and lightweight display,
overlay, or analysis settings publishes separate camera and local-update
envelopes with the same committed scene and versions. Consumers must process
both kinds; the local-update envelope does not apply camera fields. Geometry
transactions continue to deliver the camera in their complete frame.
Flat ORTEP also queues its combined image in the background because its
camera basis is rasterized into the image.
When a scene has no saved camera, the mixed camera envelope contains only
the requested projection; the saved camera remains unset and the browser
keeps its live pose.

For local baseline measurements, `window.__mv_view_diag` reports submissions,
full frames, local patches, camera updates, and stale drops.
