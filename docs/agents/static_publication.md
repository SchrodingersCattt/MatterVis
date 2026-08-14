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

For the garnet verification structure, the half-open rule produces 24 O8,
16 O6, and 24 O4 polyhedra. These counts are a regression fixture, not a
universal chemistry assumption.

## Recommended material preset

Main-view polyhedra:

| CN | Fill | Alpha | Edge | Edge alpha |
|---:|---|---:|---|---:|
| 8 | `#4CB17A` | 0.52 | `#315F4B` | 0.52 |
| 6 | `#8F50C2` | 0.65 | `#59336D` | 0.52 |
| 4 | `#3D90CE` | 0.70 | `#245D7D` | 0.52 |

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

Keep native polyhedron face shading disabled. Its dark floor makes overlapping
transparent faces muddy. Use flat translucent faces, thin edges, and explicit
sphere lighting instead. Recommended line values are 0.34/0.24 for main
edge/spoke width and 0.72/0.52 for panel edge/spoke width; spoke colour is
`#465852`.

## Layout defaults

The preset reserves `[0.005, 0.280, 0.585, 0.675]` for the main structure,
`[0.620, 0.552, 0.300, 0.378]` for the legend, and a bottom band of height
0.247 for representative panels. These are normalized figure coordinates and
scale to the requested canvas. The caller owns the canvas dimensions, title,
labels, legend text, and camera.

Static PNG/PDF/SVG publication exports use Matplotlib and retain the exact
requested canvas; they do not require Kaleido. HTML remains interactive Plotly.

## Minimal call

```bash
mat-vis render garnet.cif -o garnet.png --view unit_cell \
  --publication-layout \
  --polyhedron '{"id":"o8","center":"Gd","ligand":"O","fallback_max":8}' \
  --polyhedron '{"id":"o6","center":"Zr","ligand":"O","fallback_max":6}' \
  --polyhedron '{"id":"o4","center":"Al","ligand":"O","fallback_max":4}' \
  --config style.json --width 2325 --height 1888 --scale 1
```

`style.json` should contain only project semantics and deliberate overrides:

```json
{
  "publication": {
    "preset": "dense_coordination",
    "site_styles": [
      {
        "elements": ["Ca", "Gd"],
        "colors": ["#86D533", "#2F80D9"],
        "weights": [1, 1],
        "label": "Ca / Gd (24c)"
      }
    ],
    "specs": {
      "o8": {"panel_label": "[Ca/Gd]O8"}
    }
  }
}
```

Do not add a camera to this reusable style file. Record any selected camera
with the figure provenance for that structure.

## Acceptance checks

1. Confirm canonical-cell polyhedron counts and unique ligand-vertex count.
2. Confirm every representative panel partitions all ligands into front/back
   layers.
3. Confirm mixed-site weights normalize to one.
4. Confirm saved pixel dimensions exactly match the request.
5. Inspect the final image at delivery size for muddy overlap, clipped spheres,
   missing rear ligands, unreadable labels, and accidental camera coupling.
