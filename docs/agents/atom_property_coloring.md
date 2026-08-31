# Per-atom property coloring API

MatterVis uses one backend-neutral scalar reduction, exact range, and 256-entry
LUT for CPU raster/vector, Matplotlib, Plotly, animations, and the browser.
Omitting property coloring leaves existing output, caching, and renderer
selection unchanged.

## Python

```python
from mat_viewer import AtomPropertyColorSpec, load_structure, render

source = load_structure("charged.extxyz")
result = render(
    source,
    output="charge.svg",
    backend="cpu",
    atom_property_color=AtomPropertyColorSpec(
        fields=("array:charges",),
        colormap="coolwarm",
        center=0.0,
        label="Charge",
        unit="e",
    ),
)
```

`prepare_render()` accepts the same `atom_property_color=` value. A mapping
with the dataclass fields is also accepted. `value_range=None` scans every
selected source frame and atom exactly. A supplied range clips outliers and
avoids that scan. A constant field maps to the LUT midpoint.

## Sidecar v1

```json
{
  "schema": "mattervis.atom-properties/v1",
  "source": {"sha256": null},
  "frames": {"key": "timestep", "ids": "timesteps.npy"},
  "atoms": {"key": "id", "ids": "atom_ids.npy"},
  "properties": {
    "charge": {"values": "charge.npy", "unit": "e", "components": []},
    "velocity": {
      "values": "velocity.npy",
      "unit": "angstrom/ps",
      "components": ["x", "y", "z"]
    },
    "stress": {
      "values": "stress.npy",
      "unit": "GPa",
      "components": ["xx", "yy", "zz", "yz", "xz", "xy"]
    }
  }
}
```

Paths are relative to the manifest. NPY arrays are opened read-only with pickle
disabled, and only selected properties are mapped. Static values have shape
`(N, ...)`; trajectory values have `(F, N, ...)`. A trajectory has fixed N and
explicit `frames.ids`; unused sidecar frames may remain present.

Atom key `id` or `label` requires unique values and an exact set match on every
selected frame. Missing, duplicate, or extra atoms fail. IDs may be `(N,)` or
`(F,N)`. `row` alignment has no ID array and requires the source SHA-256.

## Metadata and rendering

Plans/results record fields, effective reduction/component, unit, range and
scope, center, finite/missing counts, missing color, LUT hash, manifest hash,
and the LUT. A requested colorbar reserves the right 14% of the paper, clamped
to 72–128 CSS px for fixed-size exports. SVG/PDF bars are vector rectangles and
text. Atom-group colors override the property base. By default each bond is
split at its midpoint and each half inherits its endpoint's final atom color:
atom-group override, then property color, then the atom/element base color.
An explicit bond-group `color` overrides both halves.

```python
render(
    source,
    atom_property_color={"fields": ["array:charges"]},
    bond_groups=[{"selector": {"all": True}, "color": "#333333"}],
)
```

The same override is available from the CLI as
`--bond-group all color=#333333` and through the Web/REST bond-group API.

## Web/REST

`GET /api/v2/atom-properties?scene_id=...` returns bounded descriptors and the
active spec. It is read-only. Change coloring through:

```http
POST /api/v2/state
Content-Type: application/json

{"atom_property_color":{"fields":["array:charges"],"colormap":"viridis"}}
```

Set `atom_property_color` to `null` to restore element colors. Continuous Web
colors use one marker/vertex-color atom group plus a small number of explicit
atom-group override traces; unique LUT colors do not increase trace count.

TODO after v1:

- browser upload of one manifest and its multiple NPY files;
- Web frame switching, timeline, and trajectory playback.
