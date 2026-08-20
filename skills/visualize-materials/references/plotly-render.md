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

Do not install Chrome automatically and do not substitute CPU output after a
Plotly failure. Preserve the original diagnostic and the requested/effective
backend. Plotly preserves the RenderPlan target, view direction, screen-up, and
deterministic ranges; its API cannot reproduce the CPU near/far planes or field
of view exactly, and the result warning records that limitation. For ordinary
static output, use `--backend cpu` instead.
