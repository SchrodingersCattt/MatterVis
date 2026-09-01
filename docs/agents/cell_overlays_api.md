# Auxiliary cell overlays

MatterVis can draw any number of backend-neutral unit-cell outlines on top of a
canonical structure scene. This is intended for crystallographic comparisons,
coordinate transforms, simulation boxes, and other cases where one atomistic
scene must be related to more than one lattice frame.

Auxiliary cells are visual annotations. They do not transform atoms, rebuild
bonds, alter periodic boundary handling, or change the display mode.

## Public schema

cell_overlays is a list of objects. Every object accepts:

| Field | Type | Default | Meaning |
|---|---|---|---|
| id | string | cell_N | Stable semantic identifier |
| matrix | 3 by 3 numbers | required | Cell vectors in world Cartesian coordinates |
| origin | 3 numbers | [0, 0, 0] | World Cartesian origin |
| color | color string | #333333 | Line color |
| width_px | positive number | 1.0 | Screen-space line width |
| dash | positive-number list | [] | Alternating on/off pattern in pixels |
| alpha | number from 0 to 1 | 1.0 | Line opacity |
| depth_test | boolean | false | Whether opaque geometry can occlude the line |

Matrices must be finite and nonsingular. IDs must be unique. Unknown keys fail
instead of being ignored.

## Python API

~~~python
from mat_viewer.agent import load_structure, render
from mat_viewer.render import RenderSpec, ViewSpec

structure = load_structure("structure.cif", bond_scale=0.85)
result = render(
    structure,
    output="figure.pdf",
    backend="cpu",
    view=ViewSpec(display="unit_cell", include_boundary_replicas=True),
    render_spec=RenderSpec(show_cell=False),
    cell_overlays=[
        {
            "id": "conventional",
            "matrix": conventional_matrix,
            "origin": [0, 0, 0],
            "color": "#333333",
            "width_px": 2.4,
            "dash": [],
        },
        {
            "id": "simulation-frame",
            "matrix": transformed_matrix,
            "origin": [0, 0, 0],
            "color": "#CC79A7",
            "width_px": 2.2,
            "dash": [10, 6],
        },
    ],
)
~~~

agent.prepare_render and agent.render use the same schema. A scene mapping may
also contain the cell_overlays key. An explicit function argument wins over a
scene value; passing None preserves and validates the scene value.

Each overlay compiles to an ordinary LinePrimitive. Its semantic ID is
cell-overlay:ID and metadata identifies kind=cell_overlay. All cell corners are
included in automatic camera fitting.

## CLI

Write the same list to JSON and pass:

~~~bash
mat-vis render structure.cif -o figure.png --view unit_cell --bond-scale 0.85 --cell-overlays cells.json
~~~

The JSON root must be a list. CLI and Python callers receive the same validation
errors. Auxiliary cells require the general renderer; an explicit batch renderer
request fails rather than dropping the overlay.

## Periodic entities

The unit_cell view controls displayed periodic copies independently of cell
overlays:

- include_boundary_replicas=false keeps the strict half-open source cell.
- include_boundary_replicas=true copies a complete molecular fragment when a
  member lies near a face, edge, or corner.
- molecule-level polyhedron center_images=false keeps source centers only.
- molecule-level polyhedron center_images=true follows the displayed fragment
  images and translates the cached center, shell, and hull together.
- periodic framework context atoms are not promoted into whole-framework copies.

The polyhedron receipt reports unique_source_centers, displayed_centers, and
center_image_shifts separately. Periodic images therefore never masquerade as
new crystallographic sites.

## Backend contract

CPU raster/vector, Matplotlib, and Plotly consume the same LinePrimitive dash
field. There is no backend-specific auxiliary-cell geometry. Plotly represents
any nonempty dash sequence with its native dashed line style; CPU and
Matplotlib preserve the numeric pattern.

## Coordinate-transform example

For an ASE/LAMMPS prism rotation, a caller can compute the rotated atomistic
scene first and then annotate both frames:

~~~python
from ase.calculators.lammps.coordinatetransform import Prism

Q = Prism(M @ C).rot_mat
C_md = C @ Q
X_md = X @ Q
~~~

Only Q is applied in this example. No M replication is performed, so atom count,
volume, fractional coordinates, and chemical topology are unchanged. The
conventional matrix C and the transformed matrix C_md can then be supplied as
two cell overlays that share one origin.

## Verification

For a scientific figure, verify:

1. every matrix is expressed in the same world frame as the displayed atoms;
2. transformed and source volumes agree when the transform is rigid;
3. the auxiliary corners are inside the final camera;
4. periodic molecule and polyhedron copies carry the same integer image shifts;
5. the saved PNG/PDF is read back and inspected, not accepted from exit status.
