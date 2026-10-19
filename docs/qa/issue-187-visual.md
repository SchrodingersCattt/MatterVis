# Issue 187 visual and latency QA

The viewer keeps interaction cues deliberately small. Hover is a browser-only
inspector and a compact atom tooltip; it does not recolour a mesh or add a
second geometry layer. Persistent selection is the configured neutral
blue-grey outline (`selection_highlight`) while the browser is open. Transparent
atom, bond, polyhedron, selection, and disorder pick traces are marked with
`meta.mv_role` and all transient interaction feedback is removed at the
`FigureResult.save()` boundary, so PNG, SVG, and PDF output has deterministic
depth compositing.

## Light and dark screenshots

The screenshot fixture is the same scene rendered with these explicit styles:

```python
light = {"background": "#FFFFFF", "selection_highlight": "#74808C"}
dark = {"background": "#1B2026", "selection_highlight": "#AEBFD0"}
```

The figure layout must use the requested background for `paper_bgcolor`,
`plot_bgcolor`, and `layout.scene.bgcolor`. The atom and bond traces retain
their configured chemistry colours; only contrast-safe fast-path bonds may
switch to the configured fallback. Browser cues use the same variables in
`frontend/assets/right_click_menu.css` and `workbench.css`. Tests should set
`data-mattervis-theme="light"` or `"dark"` on the document root when a stable
capture is required; without that attribute the browser's
`prefers-color-scheme` value is used.

## Pointer/click latency

`mat_viewer.perf.interaction.measure_pointer_latency` and
`measure_click_latency` measure from the input action through a caller-supplied
paint waiter. A browser probe should call the waiter after the Plotly canvas
has painted the expected trace or inspector. Backend tests may pass a topology
counter; `require_paint_only(report)` fails if the interaction recomputed
topology. The report exposes samples, mean, nearest-rank p95, and max in
milliseconds so CI can record a real threshold without hiding event-loop
waits inside a callback timer.
