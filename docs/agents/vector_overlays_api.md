# Native world-space vector overlays

Use this API for anchored scientific vectors that must rotate with the structure
and participate in real 3D depth occlusion: ferroelectric polarization,
molecular dipoles, forces, displacement modes, magnetic moments, and contracted
hyperpolarizability vectors.

Do not use it for a corner direction key. Use the paper-coordinate compass API
when the arrow has no physical world origin.

## Public layers

```python
from mat_viewer.renderer import (
    normalize_vector_overlays,
    resolve_vector_overlays,
    vector_mesh_traces,
    vector_overlay_bounds,
    paper_vector_label_annotations,
)
from mat_viewer.render.meshes import arrow_mesh_geometry
```

- `arrow_mesh_geometry`: pure NumPy watertight shaft/head mesh.
- `resolve_vector_overlays`: coordinate-space and magnitude-policy resolution.
- `vector_mesh_traces`: opaque Plotly Mesh3d arrows with metadata.
- `vector_overlay_bounds`: generated mesh extents for viewport ownership.
- `paper_vector_label_annotations`: static orthographic paper labels at tips.

## Group contract

```json
{
  "id": "polarization",
  "name": "Polarization",
  "magnitude_mode": "scaled",
  "scale": 0.025,
  "viewport_policy": "include",
  "opacity": 1.0,
  "style": {
    "shaft_radius": 0.10,
    "head_length_ratio": 0.28,
    "head_radius_ratio": 2.2,
    "sides": 12,
    "flatshading": true
  },
  "arrows": [
    {
      "id": "cell-polarization",
      "origin": [0.5, 0.5, 0.5],
      "origin_space": "fractional",
      "vector": [0.0, 1.4, 0.2],
      "direction_space": "cartesian",
      "color": "#D55E00",
      "label": "P",
      "metadata": {"units": "uC/cm2"}
    }
  ]
}
```

Exactly one of `vector` and `end` is required for every arrow. Coordinates are
finite three-component lists. Group and arrow IDs must be unique.

## Magnitude is always explicit

There is no default magnitude interpretation:

- `absolute`: the Cartesian displacement itself is the displayed world length.
- `scaled`: display `scale * vector`; use the same scale across comparable panels.
- `normalized`: display a fixed `length` in the supplied direction.

This prevents response tensors or polarization values from being silently
interpreted as Å. `tail_offset` can move the rendered origin along the displayed
direction, but the default is zero: an arrow beginning inside an atom is
correctly hidden by that atom.

CPU and Plotly honor the same explicit arrow geometry. `shaft_radius`,
`head_length`, `head_radius`, `head_length_ratio`, `head_radius_ratio`, and
`sides` may be set at group level or overridden per arrow. Absolute head
dimensions take precedence over their ratio counterparts. CPU primitives also
retain overlay opacity and the resolved group/arrow metadata.

For vibration modes, the conventional caller policy is to centre the displayed
arrow on the equilibrium atom: set `origin = atom_position - display_vector/2`
and pass `vector = display_vector` in `absolute` mode. Dipoles, forces, and
polarization vectors usually keep the physical anchor as the tail. This is an
origin policy rather than a distinct arrow type, so callers may choose either
convention without changing mesh geometry.

For an atom-centred force, keep `origin` at the nuclear Cartesian position and
increase one documented group `scale` until the shortest retained arrow
emerges from the rendered atom. Do not use a positive `tail_offset` merely to
make an occluded force visible: that changes the displayed physical anchor.

## Coordinate spaces

`origin_space`, `direction_space`, and `end_space` independently accept
`cartesian` or `fractional`. Fractional values use MatterVis row-vector cells:

```text
cart = frac @ M
```

No minimum-image wrapping or automatic PBC replication is applied.

## CLI integration

Save one or more group objects as a JSON list, then pass that file directly:

```bash
mat-vis render structure.xyz -o mode.png --vector-overlays vectors.json
```

Static CPU output renders these arrows without Plotly, Kaleido, or Chrome.
Animated vector overlays are rejected explicitly rather than silently dropped.

## Figure integration

```python
fig = build_figure(
    scene,
    style,
    vector_overlays=groups,
    include_interaction_traces=False,
)
```

Alternatively attach `scene["vector_overlays"]`. Explicit function arguments
win. `build_row_figure(..., vector_overlays_by_scene=[...])` supports per-panel
vectors.

`viewport_policy="include"` expands ranges using the actual shaft/head mesh.
`clip` leaves the existing world viewport unchanged.

## Depth guarantee

Correct occlusion is guaranteed for opaque vector Mesh3d traces with opaque mesh
atoms/bonds. If the arrow starts inside an atom, the hidden tail is expected.
Transparency in Plotly 3D has ordering limitations; `opacity < 1` withdraws the
strong occlusion guarantee.

## Labels

Labels remain paper overlays, never `Scatter3d(mode="text")`. Automatic label
projection currently supports static orthographic figures only. It anchors text
at the projected arrow tip with a pixel offset. Live camera-following labels and
perspective paper projection are deferred.

## Per-tab state

`vector_overlays` is a supported scene-state key and can be written through
`POST /api/v2/state`. MatterVis deep-copies it onto the rendered scene so vector
content does not leak across tabs. A geometry change clears the saved camera and
bumps `camera_revision` because include-policy bounds may change.

## Deferred

The initial API does not include a Dash table editor, dedicated CRUD route, TUI
rendering, Matplotlib flat-ORTEP arrows, automatic chemistry anchors, animated
vector overlays, or automatic vector replication after structure transforms.
