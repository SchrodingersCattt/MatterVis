# CLI and backend-neutral API parity

`mat-vis render` exposes every user-facing field in the backend-neutral
`ViewSpec`, `CameraSpec`, and `RenderSpec`. The CLI uses ordinary arguments for
scalar controls and repeatable selector rules for mixed styles.

| Public capability | CLI surface |
|---|---|
| Display mode | `--view` |
| Camera axis, direction, position, target, up, projection, fit distance, FOV, orthographic scale, and clipping | `--camera-axis`, `--view-direction`, `--camera-position`, `--camera-target`, `--camera-up`, `--orthogonal` / `--perspective`, `--camera-distance`, `--field-of-view`, `--ortho-scale`, `--camera-clip` |
| Representation, shading, backend, canvas, background, atom/bond scale, visibility, cell, labels, rings, and ORTEP | Direct `mat-vis render` flags |
| Sphere and cylinder mesh quality | `--sphere-detail`, `--cylinder-sides` |
| Per-atom selector styles and materials | Repeatable `--atom-group` |
| Per-bond selector styles and paint | Repeatable `--bond-group` |
| World-space vectors | `--vector-overlays` |
| Coordination polyhedra | Repeatable `--polyhedron` |
| Frame selection and video speed | `--frame-range`, `--stride`, `--fps` |
| Physical simulation time labels | `--display-time`, `--time-step`, `--time-step-unit`, `--dump-frequency`, `--first-frame-step`, `--time-position` |

Intentionally programmatic objects remain API-only: a precompiled `RenderPlan`
and arbitrary in-memory `topology_data`. They are composition artifacts rather
than omitted end-user controls. `mat-vis render --check --json` reports the
normalized camera, mesh-quality settings, and selector groups without loading
the source.
