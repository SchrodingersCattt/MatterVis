# View update protocol

MatterVis uses one delivery envelope for Dash, HTTP fallback, and the figure
WebSocket:

```json
{
  "type": "view_update",
  "update_kind": "overlay|display|camera|geometry|analysis",
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

For local baseline measurements, `window.__mv_view_diag` reports submissions,
full frames, local patches, camera updates, and stale drops.
