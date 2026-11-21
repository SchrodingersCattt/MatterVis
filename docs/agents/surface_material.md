# Surface materials

Appearance is representation plus surface material plus lighting. This slice
adds the Blinn–Phong surface record and three presets. It does not add a
second renderer, a material manager, or per-atom material objects.

`style["material"]` is still the draw technique: `mesh` or `flat`. Flat is
the billboard/scatter path. The new field is `surface_material`.

## Presets

| id | ambient | diffuse | specular | shininess | specular color |
|---|---:|---:|---:|---:|---|
| `matte` | 0.68 | 0.32 | 0 | 32 | none |
| `hard_plastic` | 0.30 | 0.60 | 0.35 | 48 | white |
| `metal_like` | 0.22 | 0.38 | 0.75 | 96 | tinted by the base color |
| `glass_like` | 0.36 | 0.54 | 0.32 | 112 | white |

`metal` is reserved for a later PBR model. Requesting it raises; it does not
become `metal_like`. `glass` is reserved the same way: this rasterizer does
not raytrace a refractive index. Use `glass_like`.

`glass_like` is the only preset with a Fresnel rim. The other three leave
these fields neutral (`fresnel_power` 0, center 1, rim 1):

| field | `glass_like` | effect on a translucent surface |
|---|---:|---|
| `fresnel_power` | 1.5 | `(1 - \|n·v\|)^power`. 0 disables the rim |
| `fresnel_center` | 0.12 | opacity scale when the surface faces the camera |
| `fresnel_rim` | 1.6 | opacity scale at a grazing silhouette |
| `fresnel_specular` | 0.28 | extra specular coefficient at that silhouette |
| `fresnel_albedo` | 1.0 | grazing diffuse blends toward this fraction of the base color |

Opacity is still the object's opacity. The rim multiplies it. It is not a
material field, and `from_mapping` still rejects `opacity` and `light_scale`.

`MaterialSpec.as_dict()` keeps the preset `id` so a record round-trips.
`coefficient_record()` is the mapping other backends read. It has the four
Blinn–Phong coefficients, `specular_tint`, an optional `specular_color`, and
Fresnel fields only when they are not neutral. It does not include `id`,
`opacity`, or `light_scale`. The CPU lighting reader accepts a neutral
Fresnel record and raises on any other Fresnel value instead of dropping
the rim. The surface shader evaluates that rim. `unlit` draws the inherited
color and does not rewrite the record. `studio_three_point` is rejected on
CPU. The camera light stays `(-0.32, 0.42, 1.0)`.

Ambient and diffuse do not have to sum to 1. Opacity and `light_scale` are
not fields of the record. Isosurface opacity stays 0.55 unless
`isosurface_opacity` / `--isosurface-opacity` is set. Element colors and
isosurface phase or colormap colors are inherited.

Historical ambient 0.68 and diffuse 0.32, including the builtin
`isosurface_ambient` / `isosurface_diffuse` values, are defaults. Setting
`surface_material="matte"` on a style that already holds those values
succeeds. A stored pair that matches neither the historical defaults nor the
requested preset is a conflict.

```python
from mat_viewer.render.surface_material import (
    apply_surface_material,
    get_surface_material,
)
from mat_viewer import render

get_surface_material("hard_plastic")
apply_surface_material(
    {"material": "mesh", "ambient": 0.68, "diffuse": 0.32},
    "matte",
)
render(
    "structure.cif",
    output="plastic.png",
    backend="cpu",
    render_spec={"surface_material": "hard_plastic"},
)
```

CPU PNG evaluates the preset on analytic atom spheres, bond cylinders, and
isosurface triangle meshes. `matte`, and omitting the field, keep the current
default shade. Bond cylinders still use the existing screen-space radial
shade for that default; specular presets replace it with camera-space
Blinn–Phong so the highlight is visible. Wireframe strokes, ORTEP hatch, and
axes are not given a fake highlight. Polyhedra and BFDH keep their current
lighting.

On a translucent fragment the reflected specular is not multiplied by that
alpha. The compositor uses `clip(diffuse_rgb * alpha + specular_rgb, 0, 1)`
and then blends over the background. That formula still applies when a
Fresnel rim clamps the effective alpha to exactly 1, so the highlight does
not disappear at the silhouette. Opaque draws and `matte` (`specular == 0`,
no Fresnel) keep the previous straight-alpha blend, so those pixels stay the
same.

Smooth meshes (`vertex_normals` present) with specular or a Fresnel rim are
shaded per pixel: camera-space normals are interpolated and renormalized,
then diffuse, specular, and rim opacity are evaluated at the sample. The
Blinn–Phong half vector uses the same view as the Fresnel term. Orthographic
view is camera `+Z`; perspective view is `-normalize(p_camera)`. `|n·v|` is
used so a double-sided shell still rims. `matte` meshes stay on the previous
vertex shade.

`glass_like` changes opacity with view angle only while the object opacity
is below 1. Face-on centers become much clearer than the uniform opacity, so
atoms inside an isosurface stay visible at the default 0.55. Grazing
silhouettes ramp toward a stronger, saturated rim. Fully opaque atoms and
bonds stay opaque and keep a tight glossy highlight. `--isosurface-opacity
0.35` is clearer than `0.55`, and `0.70` or `0.75` is denser. SVG and PDF
still bake one color per triangle; the rim and the unattenuated specular are
the CPU PNG path.

`mat-vis render --backend plotly` can draw the Taubin mesh from
`prepare_render`, but `render.plotly` does not apply this Blinn–Phong preset
to Mesh3d lighting. `hard_plastic`, `metal_like`, and `glass_like` are
rejected for `--backend plotly` and `--backend gpu` rather than attached and
ignored. `matte` and an omitted preset stay allowed. `build_cube_figure` does
not use that mesh and does not evaluate the rim. Do not treat a Plotly image
as a match for the CPU preset.

The array batch renderer does not evaluate these presets. `mat-vis render`
keeps the batch path for `matte` and for the omitted default. Any other
preset uses the general CPU renderer. `--renderer batch` together with
`hard_plastic`, `metal_like`, or `glass_like` is an error.

## Lighting names

`LightingRig` lives in `mat_viewer.render.lighting`. This module does not
define a second rig. The CPU material shader reads
`STUDIO_SOFT_DIRECTION` from that module. `unlit` draws the inherited
color and leaves the surface record unchanged. `studio_three_point`
raises `CpuLightingError` and does not substitute `studio_soft`.
Fresnel presets are evaluated here. The lighting module's coefficient
reader rejects a non-neutral Fresnel record instead of dropping it.

| id | CPU behavior |
|---|---|
| `studio_soft` | Camera-space direction `(-0.32, 0.42, 1.0)`. Default. |
| `unlit` | No directional shade and no specular. The surface record stays put. |
| `studio_three_point` | Named multi-light contract. CPU raises. |

## Agreed visual presets

This table is a name list for later work. It is not a selectable catalog.
Only `scientific_default` can be requested today, and only by choosing
`surface_material="matte"` with `ball_stick` and the existing
`studio_soft` light. The other rows are not commands. `vdw` is not an alias
of `space_filling`: today's space-filling radii are the compact display
table, not van der Waals radii. `flat_illustration` is not a representation
yet.

| preset | representation | surface | lighting |
|---|---|---|---|
| `scientific_default` | `ball_stick` | `matte` | `studio_soft` |
| `publication_flat` | `flat_illustration` | `matte` | `unlit` |
| `skeletal` | `wireframe` | `matte` | `unlit` |
| `vdw_standard` | `vdw` | `matte` | `studio_soft` |
| `presentation_plastic` | `ball_stick` | `hard_plastic` | `studio_three_point` |
| `metallic_showcase` | `vdw` | `metal_like` | `studio_three_point` |

Comparison renders:

```bash
mat-vis render structure.cif -o matte.png --backend cpu --renderer general
mat-vis render structure.cif -o plastic.png --backend cpu --renderer general \
  --surface-material hard_plastic
mat-vis render structure.cif -o metal-like.png --backend cpu --renderer general \
  --surface-material metal_like
mat-vis render orbital.cube -o iso-plastic.png --backend cpu \
  --surface-material hard_plastic
```
