# Native GPU PNG backend

MatterVis exposes the optional native backend through the same immutable
`RenderPlan` used by the CPU and Plotly adapters. It is selected explicitly:

```bash
python -m pip install "matter-vis[gpu]"
mat-vis capabilities --require gpu --json
mat-vis render structure.cif -o figure.png --backend gpu --json
```

The first release uses `wgpu` for offscreen PNG output. A capability probe
imports the optional package, requests an adapter, creates a device, and
reports adapter/device identity and whether the adapter is hardware backed.
Probe results are available from `mat-vis capabilities --json` and
`--require gpu --json`.

The renderer lowers immutable triangle meshes and one-pixel, opaque,
depth-tested line segments into validated `GpuDrawPacket` objects. Transparent
primitives, dashed or depth-disabled lines, text, and other unsupported
features raise `GPUUnsupportedError` before a device is used. Missing
dependencies or devices raise `GPUUnavailableError`; no CPU fallback is
attempted. GIF/MP4, HTML, PDF, and SVG remain on their existing explicit
backends.

The Python seam is `mat_viewer.render.gpu`: `probe()`, `lower_plan()`,
`render()`, and the lifecycle-compatible `GpuSession`/`GPUBackend` wrapper.
Results retain the original plan fingerprint and include requested/actual
backend metadata, GPU identity, packet count, and stage timing fields.
