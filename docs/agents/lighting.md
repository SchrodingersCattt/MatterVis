# Lighting

Appearance is representation plus surface material plus lighting. This page is
the lighting axis. Surface presets (`matte`, `hard_plastic`, `metal_like`)
are a separate record, documented with that slice in
[`surface_material.md`](surface_material.md) once it is present. Isosurface
ambient and diffuse coefficients stay on the cube object; see
[`cube_api.md`](cube_api.md).

Looking up a name does not change a picture. `get_lighting_rig` does not
retune the CPU light. The CPU general renderer changes only when a render
passes the rig, through `RenderSpec.lighting` or `mat-vis render --lighting`.

`--surface-material hard_plastic` or `metal_like` does not select
`studio_three_point`. Those presets, when the surface-material slice is
available, stay on whatever rig you set. The default rig is `studio_soft`.
`--publication-style` is rejected by `mat-vis render`. It is not a lighting
alias.

`style["material"]` remains `mesh` or `flat` (the Dash `#material-selector`:
3D mesh versus flat billboards). `fast_rendering` and `material="flat"` are
not `unlit` and are not surface presets. This slice does not add
`#lighting-selector`.

## Rigs

| id | What the CPU general renderer does |
|---|---|
| `studio_soft` | The current camera-space directional light `(-0.32, 0.42, 1.0)`. Default. Not retuned. |
| `unlit` | No directional shade and no Blinn–Phong highlight. Inherited colors are drawn flat. |
| `studio_three_point` | Named multi-light contract only. CPU raises. It does not substitute `studio_soft`. |

`light_scale` is a field of the rig, not of a surface material. Every named
rig uses `1`, so `studio_soft` keeps its historical intensity. The CPU path
rejects a non-unit scale instead of quietly rescaling the light.

`studio_three_point` stores no CPU light directions. A later consumer has to
evaluate a real three-light rig. The first one is PyVista. A CPU-only alias
or a baked highlight does not count.

## Publication

`studio_soft` is the publication default for ball-and-stick, orbitals,
electron density, and Hirshfeld surfaces. With the historical matte response
(ambient 0.68, diffuse 0.32, specular 0) it keeps shape and depth without a
hard specular that shows triangles.

`unlit` is an option for color-critical NCI and IRI plots, where shading can
distort sign(λ₂)ρ. It is not the journal default for orbitals, density, or
Hirshfeld surfaces. Those look flat and draft-like without shading.

`studio_three_point` is not a CPU publication mode in this slice.

## CLI and Python

```bash
mat-vis render structure.cif -o soft.png --backend cpu --renderer general
mat-vis render structure.cif -o flat.png --backend cpu --renderer general \
  --lighting unlit
```

`--lighting studio_three_point` is a valid name and a CPU error. `--check`
records the requested id; it does not draw a three-light figure. The array
batch renderer only runs for `studio_soft`. `unlit` uses the general CPU
renderer. `--renderer batch` together with another rig is an error, so the
batch light cannot silently stand in for it.

```python
from mat_viewer.render.contracts import RenderSpec
from mat_viewer.render.lighting import get_lighting_rig

get_lighting_rig("studio_soft")  # lookup only; does not retune the light
render(
    "structure.cif",
    output="flat.png",
    backend="cpu",
    render_spec=RenderSpec(representation="ball_stick", lighting="unlit"),
)
```

Omitting `lighting` and setting `studio_soft` are the same plan and the same
pixels.

## Composition

The rig supplies lights. A primitive `surface_material` record supplies the
surface-contract coefficients: ambient, diffuse, specular, shininess,
specular tint, and an optional specular color. Preset names are not resolved
in this module. `studio_soft` evaluates a non-matte record under the single
historical light. It does not rewrite those coefficients and it does not turn
a specular material into three lights.

Fresnel fields (`fresnel_power`, `fresnel_center`, `fresnel_rim`,
`fresnel_specular`, `fresnel_albedo`) are part of the same surface contract.
A neutral rim is accepted and ignored. Any other value raises
`CpuLightingError`. The shader does not zero the rim and continue, and it
does not fold `light_scale` into diffuse or specular. Plotly and GPU reject
every rig other than `studio_soft` instead of keeping their own lights under
a requested name.

A record that is exactly the historical matte pair keeps today's shade:
analytic spheres stay on `0.68 + 0.32 * abs(n·l)`, bonds keep
`0.72 + 0.28 * radial`, and an isosurface keeps its own ambient/diffuse dict
when that dict is what the mesh already carries. `unlit` ignores both the
Lambert term and the radial bond shade, and it leaves the material record in
place.

Objects that were never on this rig stay there. BFDH keeps its Plotly lighting
dict (ambient 0.75, diffuse 0.9, specular 0.2, roughness 0.25). Plotly
isosurface traces are not given a new default. `world_sun`, environment
lighting, shadows, and ambient occlusion are not part of this slice.

## Agreed visual presets

Only the lighting column is defined here. `flat_illustration` and `vdw` are
not representations in this slice, and `space_filling` is not a van der Waals
alias. Test lighting on `ball_stick`.

| preset | lighting |
|---|---|
| `scientific_default` | `studio_soft` |
| `publication_flat` | `unlit` |
| `skeletal` | `unlit` |
| `vdw_standard` | `studio_soft` |
| `presentation_plastic` | `studio_three_point` |
| `metallic_showcase` | `studio_three_point` |
