# Static Dense-Coordination Publication Figures

Use this contract for a full-cell coordination-polyhedron view with isolated
representative polyhedra, a legend, and a lattice compass. It is implemented by
the deterministic Matplotlib compositor behind `--publication-layout`.

The `dense_coordination` preset deliberately contains no camera. Let the
renderer use its ordinary fallback or supply a structure-specific camera at
the call site; never copy reference-figure angles into the material preset.

## Geometry and compositing contract

- Select polyhedra by centre fractional coordinate in the half-open cell
  `0 <= f < 1`. Do not include both faces of a periodic boundary.
- Keep all main-view faces in one depth-sorted collection so transparent
  polyhedra interleave correctly.
- Draw representative-panel ligands behind or in front of the polyhedron from
  projected depth. A successful render with all ligands on top is not accepted.
- Render mixed sites as occupancy-weighted sphere sectors, not blended colours.
- Keep centre atoms translucent in representative panels so internal spokes
  remain legible.
- Do not mutate topology data while selecting the canonical cell or a
  representative polyhedron.

Coordination counts are structure-specific regression data. Do not encode
them as universal chemistry assumptions or reusable preset values.

## Blender publication style

`--publication-style blender` supplies the measured material, lighting, sphere,
and line defaults below. The layout remains in `dense_coordination`. Repeated
`--publication-option PATH=VALUE` arguments are merged last and override either
built-in profile. `blender` names this visual profile; it does not change the
Matplotlib export backend.

Main-view polyhedra:

| CN | Fill | Alpha | Edge | Edge alpha |
|---:|---|---:|---|---:|
| 8 | `#4CB17A` | 0.34 | `#315F4B` | 0.22 |
| 6 | `#8F50C2` | 0.72 | `#59336D` | 0.28 |
| 4 | `#3D90CE` | 0.78 | `#245D7D` | 0.28 |

Representative-panel polyhedra:

| CN | Fill | Alpha | Edge | Edge alpha |
|---:|---|---:|---|---:|
| 8 | `#41F288` | 0.52 | `#3D7760` | 0.72 |
| 6 | `#A352D7` | 0.53 | `#68417D` | 0.72 |
| 4 | `#4E92D8` | 0.50 | `#276D96` | 0.72 |

Use `#FF6363` for ligand spheres, with a bright camera-aware material:
ambient 0.72, diffuse 0.28, and a `#FFF7F7` glossy highlight. Use radius
0.20 in the full cell, radius 0.30 for front panel ligands, and 0.20 for back
panel ligands. Panel centre spheres use radius 0.38 and alpha 0.28.

Keep native Matplotlib face shading disabled because its dark floor makes
overlapping transparent faces muddy. Apply MatterVis camera-aware face lighting
instead: ambient 0.45 plus diffuse 0.55, computed from each face normal while
preserving its alpha. Do not apply equal transparency or lighting contrast to
all families: CN8 is the translucent background network (light strength 0.18),
while CN6/CN4 are opaque foreground units (0.55/0.65). Put main-view edges on
their own front-facing hull triangles inside the depth-sorted face collection;
back-facing edges have zero alpha and are covered by nearer faces. Use width
0.20 and omit centre-to-ligand spokes, which accumulate into a dark web. Representative panels retain edge/spoke widths 0.72/0.52 because they show
one isolated polyhedron. Apply convex hidden-line layering there as well:
rear-only hull edges and all interior spokes are drawn before the translucent
face stack; front-facing hull edges are drawn after it. The face therefore
attenuates hidden lines naturally. Spoke colour is `#465852`.

## Layout defaults

The preset reserves `[0.005, 0.280, 0.585, 0.675]` for the main structure,
`[0.620, 0.552, 0.300, 0.378]` for the legend, and a bottom band of height
0.247 for representative panels. These are normalized figure coordinates and
scale to the requested canvas. The caller owns the canvas dimensions, title,
labels, legend text, and camera.

Static PNG/PDF/SVG publication exports use Matplotlib and retain the exact
requested canvas; they do not require Kaleido. HTML remains interactive Plotly.

## Minimal call

CI parses this example with the live `mat-vis` argument parser and resolves its
publication options. Keep it executable when CLI or preset fields change.

```bash
mat-vis render structure.cif -o figure.png --view unit_cell \
  --publication-layout \
  --polyhedron '{"id":"cn8","center":"M8","ligand":"X","fallback_max":8}' \
  --polyhedron '{"id":"cn6","center":"M6","ligand":"X","fallback_max":6}' \
  --polyhedron '{"id":"cn4","center":"M4","ligand":"X","fallback_max":4}' \
  --publication-preset dense_coordination \
  --publication-style blender \
  --width 2325 --height 1888 --scale 1
```

Reusable preset/style selection and overrides must travel through `mat-vis`
arguments. Explicit `--publication-option` values take precedence over
`blender` defaults. Structure-specific species, site labels, legend text, title, and
camera belong at the call site and must not be committed as a generic preset.
Final files must be produced by `mat-vis render`, not delivered from a direct
Python builder call. Direct API calls remain appropriate for unit tests and
renderer development.

Do not add a camera to the reusable preset. Record any selected camera
with the figure provenance for that structure.

## Acceptance checks

1. Confirm canonical-cell polyhedron counts and unique ligand-vertex count.
2. Confirm every representative panel partitions all ligands into front/back
   layers.
3. Confirm mixed-site weights normalize to one.
4. Confirm saved pixel dimensions exactly match the request.
5. Inspect the final image at delivery size for muddy overlap, clipped spheres,
   missing rear ligands, unreadable labels, and accidental camera coupling.
