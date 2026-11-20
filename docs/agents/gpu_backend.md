# Native GPU backend

Atomaestro exposes the optional native backend through the same immutable
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
opaque lines that are depth-tested use the GPU line list. Depth-disabled
lines of any width, wider strokes, including the default unit-cell edges,
and ORTEP hatch or principal-axis lines are composited together on the CPU
inside each viewport's pixel crop and camera. A depth-disabled stroke stays
in front of a depth-tested one. A plan may contain more than one panel.
Dashed lines, text, transparent primitives, per-vertex mesh colors,
per-endpoint line colors, and cube isosurface meshes raise
`GPUUnsupportedError`. An isosurface mesh is not drawn as a flat colour.
There is no CPU fallback
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
atom-property colours, are drawn from those arrays. That path honours
``RenderSpec.shading`` of ``smooth`` or ``flat``. Structure frames and every
other representation go through the planned `RenderPlan` on that same session,
so the displayed atoms match the CPU path. Instanced meshes are shaded with
the CPU camera-space light in world space. They are not pixel-identical to
the analytic CPU sphere and cylinder rasters, and they do not honour CPU
supersampling or sphere and cylinder tessellation controls.

The GPU packet stores one opaque colour. It does not define a surface
material or a lighting rig, and it does not look up preset names. A missing
`surface_material` is the historical camera-space shade. A coefficient
record is accepted only when its id is `matte`, its specular weight is 0,
and its Fresnel power is 0: that record asks for no highlight. Any other
id (`hard_plastic`, `metal_like`, `glass_like`, `metal`, `glass`, or
another name), a specular highlight, a Fresnel term, or a string where a
coefficient record belongs raises `GPUUnsupportedError`. Those presets are
not redrawn as the matte shade. A missing `lighting` value, or
`studio_soft` at `light_scale` 1, is the same historical light. `unlit`
would still be shaded by that light, so it is rejected. `studio_three_point`
is rejected and is not replaced with `studio_soft`. Any other rig name or
a non-unit `light_scale` is rejected the same way.

`raster_passes()` reports which packet ids are drawn in the GPU pass and
which are in the CPU overlay. The overlay is the intended path for lines
wider than one pixel, depth-disabled strokes, ORTEP hatch, and ORTEP
principal axes. It is not a fallback for unsupported geometry. Each
viewport is composited with its own camera and pixel crop. A depth-disabled
stroke stays in front of a later depth-tested stroke. A depth-tested stroke
is hidden where the GPU depth is nearer than the stroke. Colour from one
viewport is not drawn into another.

The Python seam is `mat_viewer.render.gpu`: `probe()`, `lower_plan()`,
`raster_passes()`, `render()`, `render_animation()`, and the
lifecycle-compatible `GpuSession`/`GPUBackend` wrapper. `close()` releases
cached instance buffers and the device. Results retain the original plan
fingerprint and include requested/actual backend metadata, GPU identity,
packet count, the raster-pass report, and stage timing fields. Animation
results report `backend: gpu`.
