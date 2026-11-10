# Native GPU backend

MatterVis exposes the optional native backend through the same immutable
`RenderPlan` used by the CPU and Plotly adapters. It is selected explicitly:

```bash
python -m pip install "matter-vis[gpu]"
mat-vis capabilities --require gpu --json
mat-vis render structure.cif -o figure.png --backend gpu --json
python -m pip install "matter-vis[gpu,animation]"
mat-vis render trajectory.extxyz -o movie.gif --backend gpu --json
```

The backend uses `wgpu` for offscreen colour and `depth32float`. A capability
probe imports the optional package, requests an adapter, creates a device, and
reports adapter/device identity and whether the adapter is hardware backed.
Probe results are available from `mat-vis capabilities --json` and
`--require gpu --json`.

Opaque triangle meshes are drawn directly. Spheres, cylinders, and solid
ellipsoids are instanced from the plan's analytic shape records. One-pixel
opaque lines use the GPU line list. Depth-disabled one-pixel lines are drawn
after opaque geometry, so a later mesh does not cover them. Wider strokes,
including the default unit-cell edges, and ORTEP hatch or principal-axis
lines are composited on the CPU inside each viewport's pixel crop and
camera. A plan may contain more than one panel. Dashed lines, text, and
transparent primitives raise `GPUUnsupportedError`. There is no CPU fallback
when the optional dependency, the device, or a primitive is unsupported.

Viewport rectangles use the CPU image convention: `y = 0` is the top.
`scale` does not supersample a GPU image; the result is `width` by `height`.
Instanced spheres use a fixed 8×12 mesh and cylinders a fixed 12-sided mesh.
`sphere_detail` and `cylinder_sides` change the CPU meshes only. Flat shading
clears vertex normals and lights each instance triangle from its face.
Smooth shading keeps per-vertex lighting. Neither mode matches the analytic
CPU sphere and cylinder rasters pixel for pixel.

PNG, GIF, and MP4 are the supported outputs. HTML, PDF, and SVG stay on their
existing explicit backends. GIF and MP4 also require `matter-vis[animation]`.
One `GpuSession` draws the whole animation, with at most one frame in flight.
Ball and ball-and-stick frames that are already `FrameBatch` arrays, and that
do not request a display mode, labels, axes, groups, overlays, topology, or
atom-property colours, are drawn from those arrays. Structure frames and every
other representation go through the planned `RenderPlan` on that same session,
so the displayed atoms match the CPU path. Instanced meshes are shaded with
the CPU camera-space light in world space. They are not pixel-identical to
the analytic CPU sphere and cylinder rasters, and they do not honour CPU
supersampling or sphere and cylinder tessellation controls.

The Python seam is `mat_viewer.render.gpu`: `probe()`, `lower_plan()`,
`render()`, `render_animation()`, and the lifecycle-compatible
`GpuSession`/`GPUBackend` wrapper. `close()` releases cached instance buffers
and the device. Results retain the original plan fingerprint and include
requested/actual backend metadata, GPU identity, packet count, and stage
timing fields. Animation results report `backend: gpu`.
