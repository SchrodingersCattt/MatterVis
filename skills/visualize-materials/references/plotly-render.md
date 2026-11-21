# Plotly HTML and Explicit Plotly Export

Use Plotly only when the caller requests interactive HTML/WebGL or explicitly
chooses Plotly static export.

Interactive HTML:

```bash
mat-vis capabilities --require html --json
mat-vis render INPUT.cif -o OUTPUT.html --backend plotly --check --json
mat-vis render INPUT.cif -o OUTPUT.html --backend plotly --json
```

HTML requires `[plotly]` and no Chrome. Explicit Plotly PNG/PDF/SVG requires
`[plotly-export]` and may require a working Chrome runtime:

```bash
mat-vis capabilities --require plotly-export --json
mat-vis render INPUT.cif -o OUTPUT.png --backend plotly --check --json
```

When static Plotly export is invoked through the running Web frontend, resolve
both boundaries instead of checking Kaleido alone:

```bash
mat-vis capabilities --require web-screenshot --json
mat-vis capabilities --require static-web-export --json
```

Both aliases report
`python -m pip install "matter-vis[plotly-export,web]"`. A failed Web export is
reported as an error; it does not return a placeholder image.

Do not install Chrome automatically and do not substitute CPU output after a
Plotly failure. Preserve the original diagnostic and the requested/effective
backend. Plotly preserves the RenderPlan target, view direction, screen-up, and
deterministic ranges; its API cannot reproduce the CPU near/far planes or field
of view exactly, and the result warning records that limitation. For ordinary
static output, use `--backend cpu` instead.

`mat-vis render --backend plotly` uses the `prepare_render` mesh. Cube
geometry is Taubin-smoothed only with `--isosurface-smooth`. It does not evaluate `--surface-material`:
`hard_plastic`, `metal_like`, and `glass_like` are rejected on `--backend plotly` and `gpu`.
`build_cube_figure` / `build_figure` / `orbital_mesh_traces` rebuild
isosurfaces with raw `marching_cubes` and flat Mesh3d lighting. That preview
is not publication quality. Journal cube figures use
`mat-vis render --backend cpu`.
